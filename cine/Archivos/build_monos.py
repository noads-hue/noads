#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arma, para el modo normal de Nimaru (MonosChinos), tres JSON ya listos para descargar:

  cine/Archivos/monos_catalogo.json    TODAS las páginas de https://monoschinos.st/animes?p=N (el directorio completo),
                                       en el mismo orden que el sitio.
  cine/Archivos/monos_generos.json     La lista de géneros del filtro de /animes y, para cada género, su primera página.
  cine/Archivos/monos_calendario.json  El calendario (lunes a domingo) y, para cada serie, su póster vertical (el que
                                       la app hoy busca serie por serie al abrir el calendario).

Es un calco de MonoschinosScraper (parseSeriesGrid, fetchGenres, parseCalendario, fetchSeriesPoster): mismas
expresiones regulares, mismos límites, para que la app muestre exactamente lo que armaría ella misma.

Si MonosChinos no responde (o Cloudflare no deja pasar), NO se escribe nada: se conservan los archivos anteriores,
y la app, pasadas 24 h sin renovarse, vuelve sola a pedir en vivo (y a su respaldo AnimeAV1) como siempre.

Uso:  python cine/Archivos/build_monos.py                 (lo corre GitHub Actions)
      python cine/Archivos/build_monos.py --carpeta cine/Archivos --solo catalogo|generos|calendario
"""
import argparse
import html as _html
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests

try:
    from curl_cffi import requests as curl_requests
    _CURL = True
except ImportError:
    _CURL = False
_tls = threading.local()

BASE = "https://monoschinos.st"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Mobile Safari/537.36")
TIMEOUT = 30
PARALELO = 4
MAX_PAGINAS = 400
REESCRIBIR_CADA_MS = 12 * 60 * 60 * 1000

CALENDAR_DAYS = [("lunes", "Lunes"), ("martes", "Martes"), ("miercoles", "Miércoles"), ("jueves", "Jueves"),
                 ("viernes", "Viernes"), ("sabado", "Sábado"), ("domingo", "Domingo")]


# ── HTTP (con la huella TLS de Chrome si hay curl_cffi; las cookies de sesión se conservan por hilo) ───────────────

def _session():
    s = getattr(_tls, "s", None)
    if s is None:
        if _CURL:
            s = curl_requests.Session(impersonate="chrome")
        else:
            s = requests.Session()
            s.headers["User-Agent"] = UA
        _tls.s = s
    return s


def get(url, referer=BASE):
    """GET (2 intentos). None si no hubo respuesta útil (como get() del scraper de la app)."""
    headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.9", "Accept-Language": "es-ES,es;q=0.9", "Referer": referer}
    for intento in range(2):
        try:
            r = _session().get(url, headers=headers, timeout=TIMEOUT)
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
            return r.text
        except Exception as e:
            print(f"  GET falló {url}: {e}", file=sys.stderr)
            time.sleep(2)
    return None


# ── Calco de los parsers (regex idénticas a MonoschinosScraper) ──────────────────────────────────────────────────

_ARTICLE = re.compile(r"<article[^>]*>([\s\S]{0,2000}?)</article>", re.I)
_HREF_ANIME = re.compile(r'href="(https://monoschinos\.st/anime/([^"]+))"', re.I)
_POSTER_DATA = re.compile(r'<img[^>]+data-src="(https://monoschinos\.st/assets/thumbs/imagen/[^"]+)"', re.I)
_POSTER_SRC = re.compile(r'<img[^>]+src="(https://monoschinos\.st/assets/thumbs/imagen/[^"]+)"', re.I)
_SERIE_DATA = re.compile(r'<img[^>]+data-src="(https://monoschinos\.st/assets/img/serie/imagen/[^"]+)"', re.I)
_SERIE_SRC = re.compile(r'<img[^>]+src="(https://monoschinos\.st/assets/img/serie/imagen/[^"]+)"', re.I)
_ANY_DATA = re.compile(r'<img[^>]+data-src="(https://monoschinos\.st/[^"]+\.(jpg|png|webp)[^"]*)"[^>]*>', re.I)
_ANY_SRC = re.compile(r'<img[^>]+src="(https://monoschinos\.st/[^"]+\.(jpg|png|webp)[^"]*)"[^>]*>', re.I)
_H3_200 = re.compile(r"<h3[^>]*>\s*([^<\n]{1,200}?)\s*</h3>", re.I)


def _g1(rx, text):
    m = rx.search(text)
    return m.group(1).strip() if m else None


def _not_placeholder(u):
    return u if (u is not None and "/img/anime.png" not in u and not u.endswith("anime.png")) else None


def parse_series_grid(html, max_items=60):
    """parseSeriesGrid() de la app (y parseRecentSeries, que es igual con tope 20)."""
    out, matched = [], set()
    for m in _ARTICLE.finditer(html):
        block = m.group(1)
        h = _HREF_ANIME.search(block)
        if not h:
            continue
        series_url, slug = h.group(1).strip(), h.group(2).strip()
        thumb_poster = _g1(_POSTER_DATA, block)
        if thumb_poster is None:
            thumb_poster = _not_placeholder(_g1(_POSTER_SRC, block))
        thumb_serie = _g1(_SERIE_DATA, block)
        if thumb_serie is None:
            thumb_serie = _not_placeholder(_g1(_SERIE_SRC, block))
        thumb_data = _g1(_ANY_DATA, block)
        thumb_src = _not_placeholder(_g1(_ANY_SRC, block))
        thumb = thumb_poster or thumb_serie or thumb_data or thumb_src or ""
        title = _g1(_H3_200, block) or ""
        if slug in matched or not title.strip():
            continue
        matched.add(slug)
        out.append({"title": title, "thumb": thumb, "seriesUrl": series_url, "slug": slug})
        if len(out) >= max_items:
            break
    return out


_GENRE_RX = re.compile(r'name="genero\[\]"\s+value="([^"]+)"[^>]*>\s*<span class="dd-check">[\s\S]*?</span>\s*<span>([^<]*)</span>', re.I)


def _from_html(s):
    """Html.fromHtml(...).toString().trim() para un nombre de género (entidades decodificadas, sin etiquetas)."""
    s = re.sub(r"<[^>]*>", "", s or "")
    s = _html.unescape(s)
    s = re.sub(r"[ \n]+", " ", s)
    return s.strip()


def _collate_key(name):
    """Orden del Collator español de Java (primero sin acentos ni mayúsculas; luego el nombre tal cual)."""
    base = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.category(c).startswith("M")).lower()
    return (base, name)


def parse_genres(html):
    """fetchGenres() de la app: los géneros del filtro de /animes; None si salen menos de 10 (la app no los usa)."""
    found, seen = [], set()
    for m in _GENRE_RX.finditer(html):
        slug, name = m.group(1).strip(), _from_html(m.group(2))
        if not name.strip() or slug in seen:
            continue
        seen.add(slug)
        found.append({"name": name, "slug": slug})
    if len(found) < 10:
        return None
    return sorted(found, key=lambda g: _collate_key(g["name"]))


_HREF_RX = _HREF_ANIME
_THUMB_DATA_ANY = re.compile(r'<img[^>]+data-src="([^"]+)"', re.I)
_H3_250 = re.compile(r"<h3[^>]*>\s*([\s\S]{1,250}?)\s*</h3>", re.I)
_EP_RX = re.compile(r"Ep\.?\s*(\d+)", re.I)
_CARD_RX = re.compile(r'<a[^>]+class="[^"]*rel-card[^"]*"[^>]*>([\s\S]{0,1500}?)</a>', re.I)


def parse_calendario(html):
    """parseCalendario() de la app: {Lunes: [...], ...} con title, thumb, animeUrl, slug, badge, day."""
    result = {}
    for day_id, label in CALENDAR_DAYS:
        sec = re.search(r'<section[^>]*data-day="' + day_id + r'"[^>]*>([\s\S]*?)</section>', html, re.I)
        if not sec:
            result[label] = []
            continue
        block = sec.group(1)
        items, matched = [], set()
        for cm in _CARD_RX.finditer(block):
            card = cm.group(0)
            h = _HREF_RX.search(card)
            if not h:
                continue
            anime_url, slug = h.group(1).strip(), h.group(2).strip()
            if slug in matched:
                continue
            matched.add(slug)
            thumb = _g1(_THUMB_DATA_ANY, card) or ""
            title_raw = _g1(_H3_250, card) or ""
            ep = _EP_RX.search(card)
            title = _html.unescape(title_raw)
            badge = f"Siguiente {ep.group(1)}" if ep else ""
            items.append({"title": title, "thumb": thumb, "animeUrl": anime_url, "slug": slug, "badge": badge, "day": label})
        result[label] = items
    return result


_OG_IMAGE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.I)


def parse_series_poster(html):
    """fetchSeriesPoster() de la app: el póster vertical de la página de una serie."""
    poster = _g1(_POSTER_DATA, html)
    if poster is None:
        poster = _g1(_SERIE_DATA, html)
    if poster is None:
        poster = _not_placeholder(_g1(_POSTER_SRC, html))
    if poster is None:
        poster = _g1(_OG_IMAGE, html)
    return poster if poster and poster.strip() else None


# ── Armado ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _fetch_page(page, intentos=3):
    for i in range(intentos):
        html = get(f"{BASE}/animes?p={page}")
        if html is not None:
            return parse_series_grid(html)
        time.sleep(2 + 2 * i)
    return None


def build_catalogo(max_paginas):
    """Todas las páginas de /animes hasta que una no traiga series nuevas (así lo hace la app al hacer scroll)."""
    first = _fetch_page(1)
    if not first:
        print("Catálogo: la página 1 no respondió o salió vacía", file=sys.stderr)
        return None
    out, vistos = [], set()
    for s in first:
        vistos.add(s["slug"]); out.append(s)
    por_pagina = len(first)
    page = 2
    while page <= max_paginas:
        lote = list(range(page, min(page + PARALELO, max_paginas + 1)))
        with ThreadPoolExecutor(max_workers=PARALELO) as pool:
            results = list(pool.map(_fetch_page, lote))
        fin = False
        for p, res in zip(lote, results):
            if res is None:
                print(f"Catálogo: la página {p} no respondió: se conserva el anterior", file=sys.stderr)
                return None
            nuevos = [s for s in res if s["slug"] not in vistos]
            if not nuevos:
                fin = True
                break
            for s in nuevos:
                vistos.add(s["slug"]); out.append(s)
        if fin:
            break
        page += len(lote)
    print(f"Catálogo: {len(out)} series, {por_pagina} por página, {min(page, max_paginas)} páginas leídas")
    return out, por_pagina


def build_generos():
    html = get(f"{BASE}/animes")
    if html is None:
        print("Géneros: /animes no respondió", file=sys.stderr)
        return None
    generos = parse_genres(html)
    if not generos:
        print("Géneros: la lista salió corta o vacía (la app usaría la suya por defecto)", file=sys.stderr)
        return None

    def primera(g):
        for i in range(2):
            h = get(f"{BASE}/animes?genero%5B%5D={quote(g['slug'])}&p=1")
            if h is not None:
                return parse_series_grid(h)
            time.sleep(1 + i)
        return None

    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        paginas = list(pool.map(primera, generos))
    out, sin = [], 0
    for g, pg in zip(generos, paginas):
        e = dict(g)
        if pg is not None:
            e["items"] = pg
        else:
            sin += 1
        out.append(e)
    print(f"Géneros: {len(out)}" + (f" ({sin} sin primera página; la app las pedirá en vivo)" if sin else ""))
    return out


def build_calendario():
    html = get(f"{BASE}/calendario")
    if html is None:
        print("Calendario: no respondió", file=sys.stderr)
        return None
    dias = parse_calendario(html)
    if not any(dias.values()):
        print("Calendario: sin series", file=sys.stderr)
        return None

    todas = [it for items in dias.values() for it in items]
    urls = list(dict.fromkeys(it["animeUrl"] for it in todas))

    def poster(url):
        h = get(url)
        return parse_series_poster(h) if h is not None else None

    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        posters = dict(zip(urls, pool.map(poster, urls)))
    con = 0
    for it in todas:
        p = posters.get(it["animeUrl"])
        if p:
            it["poster"] = p
            con += 1
    print(f"Calendario: {len(todas)} series en {sum(1 for v in dias.values() if v)} días, {con} con póster")
    return dias


# ── Escritura: solo si cambió (o si la fecha anterior tiene más de 12 h) ─────────────────────────────────────────

def _load_previous(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_if_changed(path, payload_key, payload, extra=None):
    prev = _load_previous(path)
    now_ms = int(time.time() * 1000)
    if prev is not None and prev.get(payload_key) == payload and now_ms - int(prev.get("ts") or 0) < REESCRIBIR_CADA_MS:
        print(f"  {path}: sin cambios, no se reescribe")
        return False
    doc = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ts": now_ms}
    if extra:
        doc.update(extra)
    doc["total"] = len(payload) if isinstance(payload, list) else sum(len(v) for v in payload.values())
    doc[payload_key] = payload
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"  Escrito {path} ({doc['total']} elementos)")
    return True


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--carpeta", default="cine/Archivos")
    ap.add_argument("--max-paginas", type=int, default=MAX_PAGINAS)
    ap.add_argument("--solo", choices=["catalogo", "generos", "calendario"], default=None)
    args = ap.parse_args()

    get(BASE)   # la portada primero: deja la cookie de sesión, como hace la app
    ok = 0
    t0 = time.time()
    if args.solo in (None, "catalogo"):
        print("== Catálogo ==")
        cat = build_catalogo(args.max_paginas)
        if cat is not None:
            items, por_pagina = cat
            write_if_changed(os.path.join(args.carpeta, "monos_catalogo.json"), "items", items, {"porPagina": por_pagina})
            ok += 1
    if args.solo in (None, "generos"):
        print("== Géneros ==")
        gen = build_generos()
        if gen is not None:
            write_if_changed(os.path.join(args.carpeta, "monos_generos.json"), "items", gen)
            ok += 1
    if args.solo in (None, "calendario"):
        print("== Calendario ==")
        cal = build_calendario()
        if cal is not None:
            write_if_changed(os.path.join(args.carpeta, "monos_calendario.json"), "dias", cal)
            ok += 1
    print(f"Listo en {time.time() - t0:.0f} s ({ok} archivos armados)")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
