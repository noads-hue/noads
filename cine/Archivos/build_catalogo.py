#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arma, para la app Nimaru, el Catálogo y los Géneros del modo privado ya listos para descargar (en vez de que el
celular pida página por página a veohentai.com):

  cine/Archivos/catalogo.json   TODAS las páginas de https://veohentai.com/explorar/ (sin filtros), en el mismo orden.
  cine/Archivos/generos.json    La lista de https://veohentai.com/generos/ y, para cada género, su miniatura (el póster
                                del primer episodio), su imagen propia y la primera página de episodios.

Es un calco de VeoHentaiExploreRepository de la app (mismo lector del flujo de Next.js, mismos campos, mismos
respaldos), para que la app muestre exactamente lo que mostraría ella misma.

Cada JSON lleva "ts" (cuándo se armó). Solo se reescribe si cambió el contenido (o si el anterior tiene más de
12 h), así el repo no crece con subidas iguales. Si una página falla, NO se escribe un catálogo incompleto: se
conserva el anterior.

Uso:  python cine/Archivos/build_catalogo.py                       (lo corre GitHub Actions)
      python cine/Archivos/build_catalogo.py --carpeta cine/Archivos --max-paginas 600
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Mobile Safari/537.36")
TIMEOUT = 30
PARALELO = 4                   # páginas a la vez contra veohentai (con calma: son cientos)
MAX_PAGINAS = 600              # tope de seguridad del catálogo
REESCRIBIR_CADA_MS = 12 * 60 * 60 * 1000   # aunque no cambie nada, se renueva la fecha cada 12 h

import html as _html
import os
from urllib.parse import urljoin, unquote_plus

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag, Comment, Declaration, Doctype, ProcessingInstruction, CData
    try:
        from bs4.element import Script, Stylesheet, TemplateString
    except ImportError:          # bs4 viejo
        Script = Stylesheet = TemplateString = type("_NoStr", (), {})
    _BS4 = True
except ImportError:
    _BS4 = False

VEO = "https://veohentai.com"
VEO_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": VEO + "/",
}

# Etiquetas que Jsoup trata como "bloque" al sacar el texto (mete un espacio entre bloques pegados)
_JSOUP_BLOCK = {
    "html", "head", "body", "frameset", "script", "noscript", "style", "meta", "link", "title", "frame", "noframes",
    "section", "nav", "aside", "hgroup", "header", "footer", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
    "pre", "div", "blockquote", "hr", "address", "figure", "figcaption", "form", "fieldset", "ins", "del", "dl",
    "dt", "dd", "li", "table", "caption", "thead", "tfoot", "tbody", "colgroup", "col", "tr", "th", "td", "video",
    "audio", "canvas", "details", "summary", "menu", "plaintext", "template", "article", "main", "svg", "math",
    "center", "dir", "applet", "marquee", "listing",
}
_JSOUP_WS = " \t\n\f\r "


def _get_text_page(url, headers, referer=None):
    """GET de una página HTML (2 intentos). None si no hubo respuesta útil (como httpGetText de la app)."""
    s = requests.Session()
    h = dict(headers)
    if referer:
        h["Referer"] = referer
    for intento in range(2):
        try:
            r = s.get(url, headers=h, timeout=TIMEOUT)
            if r.status_code in (429, 503):
                print(f"  HTTP {r.status_code} en {url} (intento {intento + 1})", file=sys.stderr)
                time.sleep(3 + 3 * intento)
                continue
            if r.status_code != 200:
                print(f"  HTTP {r.status_code} en {url}", file=sys.stderr)
                return None
            if "Just a moment" in r.text[:2000] and "cf-" in r.text[:5000]:
                print(f"  Cloudflare pide verificación en {url}", file=sys.stderr)
                return None
            return r.text if r.text.strip() else None
        except requests.RequestException as e:
            print(f"  GET falló {url}: {e}", file=sys.stderr)
            time.sleep(2)
    return None


def _soup(html):
    try:
        return BeautifulSoup(html, "html5lib")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _doc_base(soup, default):
    """Jsoup toma el <base href> del documento (si es absoluto) como base para absUrl()."""
    for b in soup.find_all("base"):
        href = (b.get("href") or "").strip()
        if href.startswith(("http://", "https://")):
            return href
    return default


def _abs_url(el, attr, base):
    """Element.absUrl(attr) de Jsoup: '' si no está el atributo; relativo → resuelto contra la base."""
    if not el.has_attr(attr):
        return ""
    v = (el.get(attr) or "").strip()
    if not base:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", v):
            return v
        return ""
    try:
        return urljoin(base, v)
    except ValueError:
        return v if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", v) else ""


def _attr(el, name):
    """Element.attr(name) de Jsoup: '' si no existe."""
    if el is None:
        return ""
    v = el.get(name)
    if v is None:
        return ""
    return " ".join(v) if isinstance(v, list) else v


def _jsoup_text(el):
    """Element.text() de Jsoup 1.15: texto normalizado (espacios colapsados; un espacio entre bloques y texto)."""
    if el is None:
        return ""
    out = []

    def last_ws():
        return not out or out[-1][-1:] == " "

    def walk(node):
        if isinstance(node, (Comment, Declaration, Doctype, ProcessingInstruction, CData, Script, Stylesheet,
                             TemplateString)):
            return
        if isinstance(node, NavigableString):
            buf = []
            for c in str(node):
                if c in ("\u200b", "\u00ad"):
                    continue
                if c in _JSOUP_WS:
                    if (buf and buf[-1] == " ") or (not buf and last_ws()):
                        continue
                    buf.append(" ")
                else:
                    buf.append(c)
            if buf:
                out.append("".join(buf))
            return
        if isinstance(node, Tag):
            if node.name in ("script", "style"):
                return
            is_block = node.name in _JSOUP_BLOCK
            if (is_block or node.name == "br") and not last_ws():
                out.append(" ")
            for ch in node.contents:
                walk(ch)
            if is_block and isinstance(node.next_sibling, NavigableString) and not last_ws():
                out.append(" ")

    walk(el)
    return "".join(out).strip()


def _select_first(el, selector):
    """Element.selectFirst() de Jsoup: el primero en orden de documento, contando al propio elemento."""
    if isinstance(el, Tag) and el.name and el.name != "[document]":
        try:
            import soupsieve
            if soupsieve.match(selector, el):
                return el
        except Exception:
            pass
    return el.select_one(selector)


# ── Más vistos (VeoHentaiExploreRepository.parsePage con ?orden=vistas) ──────────────────────────────────────

def _rsc_stream(html):
    """El flujo de Next.js: self.__next_f.push([1,"…"]) con el texto escapado como cadena JS (calco de rscStream)."""
    key = 'self.__next_f.push([1,"'
    out = []
    n = len(html)
    start = 0
    while True:
        s = html.find(key, start)
        if s < 0:
            break
        i = s + len(key)
        while i < n:
            c = html[i]
            if c == "\\" and i + 1 < n:
                nx = html[i + 1]
                if nx == "n":
                    out.append("\n")
                elif nx == "r":
                    out.append("\r")
                elif nx == "t":
                    out.append("\t")
                elif nx == "b":
                    out.append("\b")
                elif nx == "f":
                    out.append("\f")
                elif nx == "u":
                    cp = None
                    if i + 6 <= n:
                        hx = html[i + 2:i + 6]
                        if re.fullmatch(r"[+-]?[0-9a-fA-F]+", hx):
                            try:
                                cp = int(hx, 16)
                            except ValueError:
                                cp = None
                    if cp is not None and 0 <= cp <= 0xFFFF:
                        out.append(chr(cp))
                        i += 4
                else:
                    out.append(nx)
                i += 2
            elif c == '"':
                break
            else:
                out.append(c)
                i += 1
        start = i + 1
    s = "".join(out)
    # Los 😀 (parejas sustitutas) se juntan en un solo carácter, como hace Kotlin con su String
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _read_json_at(stream, start):
    try:
        v, _end = json.JSONDecoder(strict=False).raw_decode(stream, start)
        return v
    except Exception:
        return None


def _prim_str(v):
    """JsonObject.str(k): solo primitivos (string/número/bool), como texto."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return None


def _prim_int(v):
    """JsonPrimitive.getAsInt(): número → entero; texto numérico → entero; otra cosa → error (como Gson)."""
    if v is None or isinstance(v, (dict, list)):
        return None
    if isinstance(v, bool):
        raise ValueError("bool no es entero")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        if re.fullmatch(r"[+-]?\d+", v.strip()) and v == v.strip():
            return int(v)
        raise ValueError(f"'{v}' no es entero")
    return None


def _obj(o, k):
    v = o.get(k)
    return v if isinstance(v, dict) else None


def _parse_items(stream):
    key = '"initial":'
    idx = stream.find(key + "[")
    if idx < 0:
        return []
    arr = _read_json_at(stream, idx + len(key))
    if not isinstance(arr, list):
        return []
    out = []
    for o in arr:
        if not isinstance(o, dict):
            continue
        slug = _prim_str(o.get("slug"))
        if slug is None:
            continue
        title = _prim_str(o.get("title"))
        if title is None:
            continue
        featured, backdrop = _obj(o, "featured"), _obj(o, "backdrop")
        path = (_prim_str(featured.get("path")) if featured else None)
        if path is None:
            path = (_prim_str(backdrop.get("path")) if backdrop else None)
        if path is None:
            continue
        date = _prim_str(o.get("releaseDate"))
        if date is None:
            date = _prim_str(o.get("uploadDate"))
        year = 0
        if date is not None:
            y4 = date[:4]
            year = int(y4) if re.fullmatch(r"[+-]?\d+", y4) else 0
        series, studio = _obj(o, "series"), _obj(o, "studio")
        item_id = _prim_int(o.get("id"))
        episode = _prim_int(o.get("episodeNumber"))
        bpath = _prim_str(backdrop.get("path")) if backdrop else None
        out.append({
            "id": item_id if item_id is not None else 0,
            "slug": slug,
            "title": title,
            "episode": episode if episode is not None else 0,
            "seriesName": (_prim_str(series.get("name")) if series else None) or "",
            "imageUrl": f"{VEO}/media/{path}",
            "studioSlug": (_prim_str(studio.get("slug")) if studio else None) or "",
            "year": year,
            "backdropUrl": f"{VEO}/media/{bpath}" if bpath is not None else None,
        })
    return out


def _parse_explore_dom(html):
    """Plan B de la app si cambiara el formato: el HTML visible (sin estudio ni año)."""
    soup = _soup(html)
    base = _doc_base(soup, VEO + "/")
    out, seen = [], set()
    for a in soup.select('main a[href^="/ver/"]'):
        slug = _attr(a, "href").strip().strip("/")
        if slug.startswith("ver/"):
            slug = slug[4:]
        slug = slug.strip("/")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        img = _select_first(a, "img")
        if img is None:
            continue
        src = _abs_url(img, "src", base)
        if not src:
            continue
        h3 = _select_first(a, "h3")
        title = _jsoup_text(h3).strip() if h3 is not None else ""
        if not title:
            title = _attr(img, "alt").strip()
        m = re.search(r"(?i)episodio\s*(\d+)", title)
        ep = int(m.group(1)) if m else 0
        out.append({"id": 0, "slug": slug, "title": title, "episode": ep, "seriesName": "", "imageUrl": src,
                    "studioSlug": "", "year": 0, "backdropUrl": None})
    return out


def parse_explore_page(html):
    """VeoHentaiExploreRepository.parsePage(): los episodios de una página de /explorar/ (None si no se pudo leer)."""
    try:
        items = _parse_items(_rsc_stream(html))
    except Exception as e:
        print(f"  Más vistos: no se pudo leer la página: {e}", file=sys.stderr)
        return None
    if items:
        return items
    try:
        return _parse_explore_dom(html)
    except Exception as e:
        print(f"  Más vistos: no se pudo leer el HTML: {e}", file=sys.stderr)
        return None


# ── Catálogo: todas las páginas de /explorar/ ─────────────────────────────────────────────────────────────────

def parse_page_full(html):
    """VeoHentaiExploreRepository.parsePage(): (items, pageCount). None si no se pudo leer."""
    try:
        stream = _rsc_stream(html)
        items = _parse_items(stream)
    except Exception as e:
        print(f"  No se pudo leer la página: {e}", file=sys.stderr)
        return None
    if items:
        start = stream.find('"initial":[')
        m = re.compile(r'"pageCount":(\d+)').search(stream, max(start, 0))
        pc = int(m.group(1)) if m else 1
        return items, pc
    try:
        soup = _soup(html)
        items = _parse_explore_dom(html)
        pcs = []
        for a in soup.select('a[href*="pagina="], a[href*="/page/"]'):
            m = re.search(r"(?:pagina=|/page/)(\d+)", _attr(a, "href"))
            if m:
                pcs.append(int(m.group(1)))
        return items, (max(pcs) if pcs else 1)
    except Exception as e:
        print(f"  No se pudo leer el HTML: {e}", file=sys.stderr)
        return None


def explore_url(page):
    return VEO + "/explorar/" + ("" if page <= 1 else f"?pagina={page}")


def fetch_explore_page(page, intentos=3):
    for i in range(intentos):
        html = _get_text_page(explore_url(page), VEO_HEADERS)
        if html:
            parsed = parse_page_full(html)
            if parsed is not None:
                return parsed
        time.sleep(2 + 2 * i)
    return None


def build_catalogo(max_paginas):
    """(items, pageCount, porPagina) o None si alguna página no se pudo bajar (no se arma un catálogo a medias)."""
    first = fetch_explore_page(1)
    if first is None or not first[0]:
        print("Catálogo: la página 1 no respondió", file=sys.stderr)
        return None
    items1, page_count = first
    total_paginas = min(page_count, max_paginas)
    print(f"Catálogo: {page_count} páginas (se bajan {total_paginas}), {len(items1)} por página")

    paginas = {1: items1}
    pendientes = list(range(2, total_paginas + 1))
    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        for p, res in zip(pendientes, pool.map(fetch_explore_page, pendientes)):
            paginas[p] = res[0] if res is not None else None

    fallidas = [p for p, v in paginas.items() if v is None]
    if fallidas:
        print(f"Catálogo: {len(fallidas)} páginas sin respuesta ({fallidas[:10]}…): se conserva el anterior", file=sys.stderr)
        return None

    out, vistos = [], set()
    for p in range(1, total_paginas + 1):
        for it in paginas[p]:
            if it["slug"] in vistos:          # el sitio movió un episodio de página mientras se bajaba
                continue
            vistos.add(it["slug"])
            out.append(it)
    return out, page_count, len(items1)


# ── Géneros: /generos/ + primera página de cada género (calco de parseGenres / genreThumbnail / genrePage) ───────

def _plain(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.category(c).startswith("M")).lower()


def _name_from_slug(slug):
    if slug.startswith("hentai-"):
        slug = slug[len("hentai-"):]
    return " ".join(w[:1].upper() + w[1:] for w in slug.split("-"))


def _parse_genres_rsc(stream):
    out = {}
    key = '"genres":'
    start = 0
    while not out:
        i = stream.find(key + "[", start)
        if i < 0:
            break
        start = i + len(key)
        arr = _read_json_at(stream, i + len(key))
        if not isinstance(arr, list):
            continue
        for o in arr:
            if not isinstance(o, dict):
                continue
            slug = _prim_str(o.get("slug"))
            name = _prim_str(o.get("name"))
            if slug is None or name is None:
                continue
            if slug not in out:
                out[slug] = {"name": name, "slug": slug, "description": _prim_str(o.get("description")) or ""}
    return out


_COUNT_RE = re.compile(r"(\d[\d.,]*)\s*episodios?\s*$", re.I)
_REPEATED_RE = re.compile(r"^(\S.{0,38}?)\s+\1")


def _parse_genres_dom(html):
    """Devuelve (géneros por slug en orden, imágenes propias por slug)."""
    soup = _soup(html)
    base = _doc_base(soup, VEO + "/")
    out, images = {}, {}
    for a in soup.select('a[href*="/genero/"]'):
        href = _attr(a, "href").split("?", 1)[0].strip().strip("/")
        slug = href.split("genero/", 1)[1] if "genero/" in href else ""
        if not slug or "/" in slug:
            continue
        full = re.sub(r"\s+", " ", _jsoup_text(a)).strip()
        if not full:
            continue
        m = _COUNT_RE.search(full)
        body = (full[:m.start()] + full[m.end():]).strip() if m else full
        rep = _REPEATED_RE.match(body)
        h = _select_first(a, "h2, h3")
        tag_name = _jsoup_text(h).strip() if h is not None else ""
        tag_desc = ""
        for p in a.select("p"):
            if p.select_one(".pill-count") is None:
                tag_desc = _jsoup_text(p).strip()
                break
        if tag_name:
            name, desc = tag_name, tag_desc
        elif rep:
            name = rep.group(1).strip()
            desc = body[rep.end():].strip()
        else:
            guess = _name_from_slug(slug)
            if _plain(body).startswith(_plain(guess)):
                name = body[:len(guess)].strip()
                desc = body[len(guess):].strip()
            else:
                name, desc = guess, body
        img = _select_first(a, "img")
        if img is not None:
            src = _abs_url(img, "src", base)
            if src and slug not in images:
                images[slug] = src
        existing = out.get(slug)
        if existing is not None and len(existing["description"]) >= len(desc):
            continue
        if existing is None:
            out[slug] = {"name": name, "slug": slug, "description": desc}
        else:
            out[slug] = {"name": name, "slug": slug, "description": desc}   # misma posición (dict conserva el orden)
    return out, images


def parse_genres(html):
    """VeoHentaiExploreRepository.parseGenres(): lista de {name, slug, description} en el orden de la página."""
    from_rsc = _parse_genres_rsc(_rsc_stream(html))
    from_dom, images = _parse_genres_dom(html)
    order = list(from_dom.keys()) if from_dom else list(from_rsc.keys())
    out = []
    for slug in order:
        r, d = from_rsc.get(slug), from_dom.get(slug)
        name = (r["name"] if r and r["name"].strip() else None) or (d["name"] if d else None)
        if name is None:
            continue
        desc = (r["description"] if r and r["description"].strip() else None) or (d["description"] if d else "")
        out.append({"name": name, "slug": slug, "description": desc})
    return out, images


def fetch_genre_page1(slug, intentos=2):
    """genrePage(slug, 1): (items, pageCount) o None."""
    for i in range(intentos):
        html = _get_text_page(f"{VEO}/genero/{slug}/", VEO_HEADERS)
        if html:
            parsed = parse_page_full(html)
            if parsed is not None and parsed[0]:
                return parsed
        time.sleep(0.4 + i)
    return None


def build_generos():
    html = _get_text_page(VEO + "/generos/", VEO_HEADERS)
    if not html:
        print("Géneros: /generos/ no respondió", file=sys.stderr)
        return None
    try:
        generos, images = parse_genres(html)
    except Exception as e:
        print(f"Géneros: no se pudo leer la lista: {e}", file=sys.stderr)
        return None
    if not generos:
        print("Géneros: la lista salió vacía", file=sys.stderr)
        return None
    print(f"Géneros: {len(generos)}")

    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        paginas = list(pool.map(lambda g: fetch_genre_page1(g["slug"]), generos))

    out, sin_pagina = [], 0
    for g, pg in zip(generos, paginas):
        entry = {"name": g["name"], "slug": g["slug"], "description": g["description"], "image": images.get(g["slug"])}
        if pg is not None:
            items, pc = pg
            first = items[0]
            entry["thumb"] = first["imageUrl"] if first["imageUrl"].strip() else first.get("backdropUrl")
            entry["pageCount"] = pc
            entry["items"] = items
        else:
            sin_pagina += 1
        out.append(entry)
    if sin_pagina:
        print(f"Géneros: {sin_pagina} sin primera página (la app los pedirá en vivo)", file=sys.stderr)
    return out


# ── Escritura: solo si cambió (o si la fecha anterior tiene más de 12 h) ─────────────────────────────────────────

def _load_previous(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _limpiar(items):
    return [{k: v for k, v in it.items() if v is not None} for it in items]


def write_if_changed(path, items, extra=None):
    prev = _load_previous(path)
    now_ms = int(time.time() * 1000)
    nuevos = _limpiar(items)
    if prev is not None and prev.get("items") == nuevos and now_ms - int(prev.get("ts") or 0) < REESCRIBIR_CADA_MS:
        print(f"  {path}: sin cambios, no se reescribe")
        return False
    doc = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ts": now_ms, "total": len(nuevos)}
    if extra:
        doc.update(extra)
    doc["items"] = nuevos
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"  Escrito {path} ({len(nuevos)} elementos)")
    return True


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default="cine/Archivos")
    ap.add_argument("--max-paginas", type=int, default=MAX_PAGINAS)
    ap.add_argument("--solo", choices=["catalogo", "generos"], default=None, help="armar solo uno (para probar)")
    args = ap.parse_args()
    if not _BS4:
        print("Falta beautifulsoup4 (pip install beautifulsoup4 html5lib)", file=sys.stderr)
        sys.exit(1)

    ok = 0
    t0 = time.time()
    if args.solo != "generos":
        print("== Catálogo ==")
        cat = build_catalogo(args.max_paginas)
        if cat is not None:
            items, page_count, por_pagina = cat
            print(f"Catálogo: {len(items)} episodios en {time.time() - t0:.0f} s")
            write_if_changed(os.path.join(args.carpeta, "catalogo.json"), items,
                             {"paginas": page_count, "porPagina": por_pagina})
            ok += 1
    if args.solo != "catalogo":
        print("== Géneros ==")
        t1 = time.time()
        gen = build_generos()
        if gen is not None:
            print(f"Géneros armados en {time.time() - t1:.0f} s")
            write_if_changed(os.path.join(args.carpeta, "generos.json"), gen)
            ok += 1
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
