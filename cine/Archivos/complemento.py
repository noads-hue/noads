#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Completa las fichas de monos_series.json (modo normal de Nimaru) con lo que MonosChinos no trae, ya en español:

  · el título, la descripción y la miniatura de cada episodio, y el logo de la serie: de AniList (para encontrar la serie
    por su título) y de api.ani.zip (los datos por episodio, que son los mismos que usan Miruro y compañía);
  · la traducción al español de esos textos (vienen en inglés), con la API de Claude. Cada texto se traduce UNA sola vez:
    la traducción queda en traducciones.json y de ahí sale las próximas veces, aunque la serie se vuelva a armar.

Archivos (en la misma carpeta que monos_series.json):
  anilist_series.json   slug de MonosChinos → serie de AniList (id y título), o que no está (se reintenta a los días)
  traducciones.json     hash del texto original → traducción

La clave de Claude llega por la variable de entorno CLAUDE_KEY (secreto del repositorio); nunca se escribe ni se imprime.
Sin clave, o si Claude no responde, las fichas salen igual con los textos en inglés y se traducen en la próxima corrida.
Nada de esto detiene el armado: cualquier fallo deja la ficha como la dio MonosChinos.
"""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

import requests

ANILIST_URL = "https://graphql.anilist.co"
ANIZIP_URL = "https://api.ani.zip/mappings?anilist_id={}"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_VERSION = "2023-06-01"
MODELO = "claude-haiku-5-5"            # se puede cambiar con la variable de entorno CLAUDE_MODELO
UA = "noads-nimaru/1 (+https://github.com/noads-hue/noads)"
TIMEOUT = 40

ARCHIVO_SERIES = "anilist_series.json"
ARCHIVO_TRADUCCIONES = "traducciones.json"

PUNTOS_MINIMOS = 60               # parecido mínimo para dar por buena una serie de AniList
REINTENTAR_NEGATIVO_MS = 7 * 24 * 3_600_000   # cuánto se espera para volver a buscar una serie que no se encontró
MAX_CONSULTAS_ANILIST = 150       # consultas a AniList por corrida (lo que no alcance, la próxima)
MAX_TEXTOS = 600                  # textos que se traducen por corrida (lo que no alcance, la próxima)
MAX_CARACTERES = 300_000          # y como mucho estos caracteres
LOTE = 20                         # textos por pedido a Claude
LOTE_CARACTERES = 8_000
MAX_TEXTO = 3_000                 # un texto más largo no se traduce (no es una descripción normal)
MAX_FALLOS_CLAUDE = 3             # pedidos seguidos fallidos: se deja de traducir en esta corrida

INSTRUCCION = ("Eres traductor de fichas de anime. Recibes un arreglo JSON de textos en inglés (títulos o descripciones de "
               "episodios o de series). Devuelve SOLO un arreglo JSON de la misma cantidad de textos, en el mismo orden, con cada "
               "uno traducido al español neutro (el que se entiende en toda Latinoamérica). Conserva los nombres propios y el "
               "sentido; no agregues, resumas ni comentes nada; sin comillas tipográficas ni bloques de código.")

_CONSULTA = """
query ($q: String) { Page(perPage: 8) { media(search: $q, type: ANIME, sort: SEARCH_MATCH) {
  id title { romaji english native } synonyms format seasonYear episodes
  coverImage { extraLarge large } bannerImage description(asHtml: false) } } }"""

_RELACIONES = """
query ($id: Int) { Media(id: $id) { relations { edges { relationType node {
  id title { romaji english native } synonyms format seasonYear episodes startDate { year month day }
  coverImage { extraLarge large } bannerImage description(asHtml: false) } } } } }"""

FORMATOS_SERIE = ("TV", "TV_SHORT", "ONA")


# ── Títulos ───────────────────────────────────────────────────────────────────────────────────────────────────────

def normalizar(t):
    """Para comparar títulos: sin acentos, en minúsculas y solo letras y números, separados por un espacio (como la app)."""
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def parecido(q, cand):
    """Qué tanto se parecen dos títulos ya normalizados (el mismo cálculo que MiruroScraper en la app): 100 iguales; 60–100 si
    uno contiene al otro como palabras completas; 24–40 si comparten casi todas las palabras; 0 si no se parecen."""
    if not q or not cand:
        return 0
    if q == cand:
        return 100
    corto, largo = (q, cand) if len(q) <= len(cand) else (cand, q)
    if len(corto) >= 4 and f" {corto} " in f" {largo} ":
        return 60 + 40 * len(corto) // len(largo)
    a, b = set(q.split()), set(cand.split())
    comunes, total = len(a & b), len(a | b)
    if comunes >= 2 and comunes * 10 >= total * 8:          # casi todas las palabras (romanizaciones distintas: "reijo"/"reijou")
        return 60 + 40 * comunes // total
    return 40 * comunes // total if comunes >= 2 and comunes * 10 >= total * 6 else 0


_ORDINAL = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6, "septima": 7, "octava": 8, "novena": 9, "decima": 10}
_ROMANOS = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9}
_TEMPORADA = re.compile(
    r"\b(?:(?:temporada|season|parte|part|cour)\s*(\d{1,2})|(\d{1,2})\s*(?:nd|rd|th|st|a|ra|da|era)?\s*(?:season|temporada|parte|part|cour)"
    r"|(" + "|".join(_ORDINAL) + r")\s*(?:season|temporada|parte|part|cour)|s(\d{1,2}))\b")
_TEMPORADA_FINAL = re.compile(r"\s(?:([2-9])|(" + "|".join(_ROMANOS) + r"))$")   # "… 2", "… III" al final


def temporada_de(titulo):
    """("título sin la marca", número de temporada): "X temporada 2" → ("x", 2); "X 2nd Season", "X S2", "X 2", "X II" → ("x", 2);
    sin marca → ("x", 1). El título devuelto ya está normalizado."""
    t = normalizar(titulo)
    n = 1
    m = _TEMPORADA.search(t)
    if m:
        n = int(m.group(1) or m.group(2) or m.group(4) or _ORDINAL[m.group(3)])
        t = " ".join((t[: m.start()] + " " + t[m.end():]).split())
    else:
        m = _TEMPORADA_FINAL.search(t)
        if m and len(t[: m.start()]) >= 4:
            n = int(m.group(1)) if m.group(1) else _ROMANOS[m.group(2)]
            t = t[: m.start()].strip()
    return t, n


def titulos_de(item):
    """Los títulos con que se busca una serie de MonosChinos: el de la serie, los alternativos de su ficha (separados por
    " / " o " · ") y el que se adivina del slug."""
    out = []
    eps = item.get("eps") or []
    if eps and len(eps[0]) > 1:
        out.append(eps[0][1])
    alt = (item.get("info") or {}).get("a") or ""
    out.extend(p.strip() for p in re.split(r"\s+[/·|]\s+", alt) if p.strip())
    slug = re.sub(r"-(sub-espanol|latino|espanol)$", "", (item.get("slug") or "").strip().rstrip("/").rsplit("/", 1)[-1])
    out.append(slug.replace("-", " "))
    vistos, unicos = set(), []
    for t in out:
        k = normalizar(t)
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(t)
    return unicos


def _nombres(media):
    t = media.get("title") or {}
    return [x for x in (t.get("romaji"), t.get("english")) if x], [x for x in (media.get("synonyms") or []) if x]


def temporada_candidato(media):
    """La temporada que dice el título de una serie de AniList (romaji o inglés); 1 si no dice."""
    n = 1
    for nombre in _nombres(media)[0]:
        _, k = temporada_de(nombre)
        n = max(n, k)
    return n


def puntuar(media, base, n):
    """Cuánto se parece una serie de AniList al título [base] (normalizado, sin marca de temporada) de la temporada [n]:
    0 si es de otra temporada."""
    if temporada_candidato(media) != n:
        return 0
    principales, sinonimos = _nombres(media)
    p = max((parecido(base, temporada_de(x)[0]) for x in principales), default=0)
    # los sinónimos ayudan (títulos en otros idiomas), pero no por encima de un título principal igual
    ps = max((parecido(base, temporada_de(x)[0]) for x in sinonimos), default=0)
    return max(p, min(ps, 90))


# ── AniList ───────────────────────────────────────────────────────────────────────────────────────────────────────

class AniList:
    """Búsqueda y relaciones (secuelas) en AniList, con el límite de consultas por corrida y respeto del límite del sitio."""

    def __init__(self, maximo=MAX_CONSULTAS_ANILIST, pedir=None):
        self.maximo = maximo
        self.hechas = 0
        self.fallos = 0              # consultas que fallaron (red, HTTP): con una, lo no encontrado no se da por inexistente
        self.agotado = False
        self._pedir = pedir or self._pedir_http

    def _pedir_http(self, query, variables):
        r = requests.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=TIMEOUT,
                          headers={"Accept": "application/json", "User-Agent": UA})
        if r.status_code == 429:
            espera = min(int(r.headers.get("Retry-After") or 60), 90)
            print(f"  AniList: límite alcanzado, se espera {espera} s", file=sys.stderr)
            time.sleep(espera)
            r = requests.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=TIMEOUT,
                              headers={"Accept": "application/json", "User-Agent": UA})
        if r.status_code != 200:
            raise RuntimeError(f"AniList HTTP {r.status_code}")
        restante = r.headers.get("X-RateLimit-Remaining")
        if restante is not None and restante.isdigit() and int(restante) < 5:
            time.sleep(20)
        else:
            time.sleep(0.7)
        return r.json().get("data") or {}

    def _consulta(self, query, variables):
        if self.hechas >= self.maximo:
            self.agotado = True
            return None
        self.hechas += 1
        try:
            return self._pedir(query, variables)
        except Exception as e:
            self.fallos += 1
            print(f"  AniList: {type(e).__name__}: {e}", file=sys.stderr)
            return None

    def buscar(self, q):
        data = self._consulta(_CONSULTA, {"q": q})
        if data is None:
            return None
        return ((data.get("Page") or {}).get("media")) or []

    def secuela(self, id_):
        """La secuela (serie, no película ni especial) de la serie [id_] en AniList, o None."""
        data = self._consulta(_RELACIONES, {"id": id_})
        if data is None:
            return None
        bordes = (((data.get("Media") or {}).get("relations") or {}).get("edges")) or []
        series = [e["node"] for e in bordes if e.get("relationType") == "SEQUEL" and (e.get("node") or {}).get("format") in FORMATOS_SERIE]
        if not series:
            return None

        def fecha(m):
            d = m.get("startDate") or {}
            return (d.get("year") or 9999, d.get("month") or 99, d.get("day") or 99, m.get("id") or 0)
        return min(series, key=fecha)


def resumen_de(media):
    """Lo que se guarda de una serie de AniList."""
    t = media.get("title") or {}
    return {
        "id": media.get("id"),
        "titulo": t.get("romaji") or t.get("english") or "",
        "english": t.get("english") or "",
        "poster": ((media.get("coverImage") or {}).get("extraLarge")) or ((media.get("coverImage") or {}).get("large")) or "",
        "banner": media.get("bannerImage") or "",
        "descripcion": limpiar_html(media.get("description") or ""),
    }


def _orden(media, puntos):
    """Para desempatar: primero el parecido, luego que sea una serie (no OVA ni película) y la que más episodios tiene."""
    return (puntos, 1 if media.get("format") in FORMATOS_SERIE else 0, media.get("episodes") or 0)


def resolver(anilist, titulos):
    """La serie de AniList que corresponde a los [titulos] de una serie de MonosChinos ([titulos_de]), o None si ninguna se
    parece lo suficiente. La temporada es la mayor que digan los títulos o el slug ("X temporada 2", "x-2"): se busca como
    tal y, si AniList no la encuentra por título, siguiendo las secuelas desde la primera temporada. None también si se
    agotaron las consultas de la corrida."""
    n = max((temporada_de(t)[1] for t in titulos), default=1)
    mejor, orden = None, (0, 0, 0)
    for t in titulos:
        base, _ = temporada_de(t)
        if not base:
            continue
        consultas = [t]
        if n > 1:
            consultas = [f"{base} season {n}", f"{base} {n}", t]
        for q in consultas:
            encontradas = anilist.buscar(q)
            if encontradas is None:
                if anilist.agotado:
                    return None
                continue
            for m in encontradas:
                o = _orden(m, puntuar(m, base, n))
                if o > orden:
                    mejor, orden = m, o
            if orden[0] >= PUNTOS_MINIMOS:
                return resumen_de(mejor)
        if n > 1:
            # por las secuelas: la primera temporada y de ahí n-1 secuelas
            primera, op = None, (0, 0, 0)
            encontradas = anilist.buscar(base) or []
            for m in encontradas:
                o = _orden(m, puntuar(m, base, 1))
                if o > op:
                    primera, op = m, o
            if primera is not None and op[0] >= PUNTOS_MINIMOS:
                actual = primera
                for _ in range(n - 1):
                    actual = anilist.secuela(actual["id"])
                    if actual is None:
                        break
                if actual is not None:
                    return resumen_de(actual)
            if anilist.agotado:
                return None
    return None


# ── api.ani.zip ───────────────────────────────────────────────────────────────────────────────────────────────────

def pedir_anizip(id_):
    r = requests.get(ANIZIP_URL.format(id_), timeout=TIMEOUT, headers={"Accept": "application/json", "User-Agent": UA})
    if r.status_code != 200:
        raise RuntimeError(f"ani.zip HTTP {r.status_code}")
    return r.json()


def episodios_de(datos):
    """{número: {"titulo", "descripcion", "imagen"}} de la respuesta de ani.zip (solo lo que trae algo), y el logo."""
    out = {}
    for k, e in ((datos or {}).get("episodes") or {}).items():
        if not isinstance(e, dict):
            continue
        try:
            n = int(k)                 # la clave es el número del episodio en la serie (los especiales, "S1"…, se saltan)
        except (TypeError, ValueError):
            continue
        titulo = ((e.get("title") or {}).get("en") or "").strip()
        if re.fullmatch(r"(?i)episode\s*\d+", titulo):     # "Episode 3" no es un título
            titulo = ""
        desc = limpiar_html(e.get("overview") or "")
        img = (e.get("image") or "").strip()
        if not img.startswith("http"):
            img = ""
        if titulo or desc or img:
            out[n] = {"titulo": titulo, "descripcion": desc, "imagen": img}
    logo = ""
    for im in (datos or {}).get("images") or []:
        if (im.get("coverType") or "").lower() == "clearlogo" and (im.get("url") or "").startswith("http"):
            logo = im["url"]
            break
    return out, logo


def limpiar_html(s):
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n?\(Source:[^)]*\)\s*$", "", s)
    s = s.replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r" *\n *", "\n", re.sub(r"[ \t]+", " ", s)).strip()


# ── Traducción (Claude) ───────────────────────────────────────────────────────────────────────────────────────────

def clave_texto(texto):
    return hashlib.sha1(" ".join((texto or "").split()).lower().encode("utf-8")).hexdigest()[:20]


class Traducciones:
    """El caché de traducciones (traducciones.json) y los pedidos a Claude, con los topes de la corrida."""

    def __init__(self, carpeta, clave_api, modelo=None, pedir=None, max_textos=MAX_TEXTOS, max_caracteres=MAX_CARACTERES):
        self.path = os.path.join(carpeta, ARCHIVO_TRADUCCIONES)
        self.clave_api = clave_api
        self.modelo = modelo or os.environ.get("CLAUDE_MODELO") or MODELO
        self._pedir = pedir or self._pedir_http
        self.max_textos, self.max_caracteres = max_textos, max_caracteres
        self.textos, self.caracteres, self.fallos, self.pedidos = 0, 0, 0, 0
        self.cambio = False
        self.datos = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.datos = {k: v for k, v in d.items() if isinstance(v, str) and v}
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"  {ARCHIVO_TRADUCCIONES} ilegible, se empieza de cero: {type(e).__name__}", file=sys.stderr)

    def guardada(self, texto):
        return self.datos.get(clave_texto(texto))

    @property
    def activo(self):
        return bool(self.clave_api) and self.fallos < MAX_FALLOS_CLAUDE and self.textos < self.max_textos and self.caracteres < self.max_caracteres

    def traducir(self, textos):
        """{texto original: traducción} de los [textos] que se pudieron traducir (los ya guardados no cuestan nada)."""
        out, pendientes = {}, []
        for t in textos:
            t = (t or "").strip()
            if not t or t in out or t in pendientes:
                continue
            g = self.guardada(t)
            if g:
                out[t] = g
            elif len(t) <= MAX_TEXTO:
                pendientes.append(t)
        lote, largo = [], 0
        for t in pendientes:
            if not self.activo:
                break
            if lote and (len(lote) >= LOTE or largo + len(t) > LOTE_CARACTERES):
                out.update(self._lote(lote))
                lote, largo = [], 0
            lote.append(t)
            largo += len(t)
        if lote and self.activo:
            out.update(self._lote(lote))
        return out

    def _lote(self, lote):
        self.textos += len(lote)
        self.caracteres += sum(len(t) for t in lote)
        try:
            self.pedidos += 1
            traducidos = self._pedir(lote)
        except Exception as e:
            self.fallos += 1
            print(f"  Claude: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            return {}
        if traducidos is None:
            # respuesta cortada o mal formada: en dos mitades (si ya es un solo texto, se deja para otra vez)
            self.fallos += 1
            if len(lote) == 1:
                return {}
            mitad = len(lote) // 2
            return {**self._lote(lote[:mitad]), **self._lote(lote[mitad:])}
        self.fallos = 0
        out = {}
        for original, trad in zip(lote, traducidos):
            trad = (trad or "").strip()
            if trad:
                out[original] = trad
                self.datos[clave_texto(original)] = trad
                self.cambio = True
        return out

    def _pedir_http(self, lote):
        """Un pedido a la API; la lista de traducciones (misma cantidad y orden) o None si la respuesta no sirve."""
        cuerpo = {"model": self.modelo, "max_tokens": 8192, "system": INSTRUCCION,
                  "messages": [{"role": "user", "content": json.dumps(lote, ensure_ascii=False)}]}
        r = requests.post(CLAUDE_URL, json=cuerpo, timeout=120,
                          headers={"x-api-key": self.clave_api, "anthropic-version": CLAUDE_VERSION, "content-type": "application/json"})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        resp = r.json()
        texto = "".join(p.get("text", "") for p in resp.get("content") or [] if p.get("type") == "text")
        if resp.get("stop_reason") == "max_tokens":
            return None
        return interpretar(texto, len(lote))

    def guardar(self):
        if not self.cambio:
            return False
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.datos, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            f.write("\n")
        self.cambio = False
        return True


def interpretar(texto, cantidad):
    """La lista de traducciones que viene en la respuesta (un arreglo JSON, aunque venga con texto o un bloque de código
    alrededor); None si no hay un arreglo de [cantidad] textos."""
    s = texto or ""
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        return None
    try:
        arr = json.loads(s[i:j + 1])
    except Exception:
        return None
    if not isinstance(arr, list) or len(arr) != cantidad or not all(isinstance(x, str) for x in arr):
        return None
    return arr


# ── Las fichas ────────────────────────────────────────────────────────────────────────────────────────────────────

def _leer_json(path, defecto):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, type(defecto)) else defecto
    except Exception:
        return defecto


def _escribir_json(path, datos):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        f.write("\n")


def complementar(items, carpeta, clave_api=None, anilist=None, anizip=None, traducciones=None, ahora_ms=None):
    """Completa las fichas [items] (las de monos_series.json) en el lugar: cada episodio pasa de 6 a 8 campos
    ([…, título del episodio, descripción], en español cuando ya hay traducción) y la ficha gana el logo ("l") y el banner
    ("b"). Devuelve un resumen. [anilist], [anizip] y [traducciones] se pueden reemplazar en las pruebas."""
    ahora = ahora_ms or int(time.time() * 1000)
    anilist = anilist or AniList()
    anizip = anizip or pedir_anizip
    tr = traducciones or Traducciones(carpeta, clave_api)
    path_series = os.path.join(carpeta, ARCHIVO_SERIES)
    conocidas = _leer_json(path_series, {})
    cambio_series = False
    res = {"series": len(items), "encontradas": 0, "sin_anilist": 0, "con_datos": 0, "episodios": 0, "traducidos": 0, "en_ingles": 0}

    for item in items:
        slug = item.get("slug") or ""
        eps = item.get("eps") or []
        if not slug or not eps:
            continue
        for e in eps:                      # todos con 8 campos, haya o no datos
            while len(e) < 8:
                e.append("")
        c = conocidas.get(slug)
        if c is None or (c.get("id") is None and ahora - int(c.get("ts") or 0) > REINTENTAR_NEGATIVO_MS):
            if anilist.agotado:
                c = None
            else:
                fallos_antes = anilist.fallos
                r = resolver(anilist, titulos_de(item))
                if r is None and (anilist.agotado or anilist.fallos != fallos_antes):
                    c = None                   # no se pudo buscar bien: se vuelve a intentar la próxima corrida
                else:
                    c = {"id": r["id"] if r else None, "titulo": (r or {}).get("titulo", ""), "ts": ahora}
                    if r:
                        c.update({"poster": r["poster"], "banner": r["banner"], "descripcion": r["descripcion"]})
                    conocidas[slug] = c
                    cambio_series = True
                    print(f"  {slug}: " + (f"AniList {r['id']} ({r['titulo']})" if r else "no está en AniList"))
        if not c or c.get("id") is None:
            res["sin_anilist"] += 1
            continue
        res["encontradas"] += 1
        try:
            datos = anizip(c["id"])
        except Exception as e:
            print(f"  {slug}: ani.zip: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
            datos = None
        episodios, logo = episodios_de(datos) if datos else ({}, "")
        info = item.setdefault("info", {})
        if logo:
            info["l"] = logo
        if c.get("banner"):
            info["b"] = c["banner"]
        if not (info.get("s") or "").strip() and c.get("descripcion"):
            info["s"] = c["descripcion"]          # sin sinopsis en MonosChinos: la de AniList (se traduce abajo)
        if not episodios and not c.get("descripcion"):
            continue
        res["con_datos"] += 1

        # los textos de esta serie, traducidos de una vez (los guardados no cuestan)
        textos = []
        if info.get("s") == c.get("descripcion") and c.get("descripcion"):
            textos.append(c["descripcion"])
        usados = []
        for e in eps:
            n = _numero(e[2])
            d = episodios.get(n) if n is not None else None
            if d:
                usados.append((e, d))
                textos.extend(x for x in (d["titulo"], d["descripcion"]) if x)
        traducidos = tr.traducir(textos)
        if info.get("s") == c.get("descripcion") and c.get("descripcion"):
            info["s"] = traducidos.get(c["descripcion"], c["descripcion"])
        for e, d in usados:
            res["episodios"] += 1
            e[6] = traducidos.get(d["titulo"], d["titulo"]) if d["titulo"] else ""
            e[7] = traducidos.get(d["descripcion"], d["descripcion"]) if d["descripcion"] else ""
            if d["imagen"]:
                e[3] = d["imagen"]
            for x in (d["titulo"], d["descripcion"]):
                if x:
                    res["traducidos" if x in traducidos else "en_ingles"] += 1

    if cambio_series:
        _escribir_json(path_series, conocidas)
    tr.guardar()
    res["consultas_anilist"] = anilist.hechas
    res["pedidos_claude"] = tr.pedidos
    return res


def _numero(s):
    try:
        d = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    return int(d) if d.is_integer() and 0 <= d < 100_000 else None
