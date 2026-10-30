#!/usr/bin/env python3
"""Diagnóstico 2: AniList (búsqueda por título) + api.ani.zip (títulos, descripciones y miniaturas por episodio, logo)
desde GitHub Actions, para las primeras series de monos_series.json."""
import json, os, re, sys, time, unicodedata
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ANILIST = "https://graphql.anilist.co"
ANIZIP = "https://api.ani.zip/mappings?anilist_id={}"
Q = """
query ($q: String) { Page(perPage: 6) { media(search: $q, type: ANIME, sort: SEARCH_MATCH) {
  id idMal title { romaji english native } synonyms format seasonYear episodes status
  coverImage { extraLarge large } bannerImage description(asHtml: false) genres } } }"""


def norm(t):
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def parecido(q, cand):
    if not q or not cand:
        return 0
    if q == cand:
        return 100
    corto, largo = (q, cand) if len(q) <= len(cand) else (cand, q)
    if len(corto) >= 4 and f" {corto} " in f" {largo} ":
        return 60 + 40 * len(corto) // len(largo)
    a, b = set(q.split()), set(cand.split())
    comunes, total = len(a & b), len(a | b)
    return 40 * comunes // total if comunes >= 2 and comunes * 10 >= total * 6 else 0


def buscar(q):
    r = requests.post(ANILIST, json={"query": Q, "variables": {"q": q}}, timeout=30,
                      headers={"Accept": "application/json", "User-Agent": "noads-diag/1"})
    print(f"  AniList '{q}': HTTP {r.status_code}, límite restante {r.headers.get('X-RateLimit-Remaining')}")
    if r.status_code != 200:
        return []
    return r.json().get("data", {}).get("Page", {}).get("media", []) or []


def mejor(titulos):
    vistos, mejor, puntos = {}, None, 0
    for t in titulos:
        if not t:
            continue
        qn = norm(t)
        for m in buscar(t):
            nombres = [m["title"].get("romaji"), m["title"].get("english"), m["title"].get("native")] + (m.get("synonyms") or [])
            p = max((parecido(qn, norm(n)) for n in nombres if n), default=0)
            vistos[m["id"]] = max(vistos.get(m["id"], 0), p)
            if p > puntos:
                mejor, puntos = m, p
        time.sleep(0.7)
        if puntos >= 60:
            break
    return mejor, puntos, vistos


def main():
    items = json.load(open(os.path.join(HERE, "..", "cine", "Archivos", "monos_series.json"), encoding="utf-8"))["items"]
    salida = []
    for it in items[:12]:
        titulo = it["eps"][0][1] if it["eps"] else it["slug"]
        alt = (it.get("info") or {}).get("a") or ""
        print(f"\n== {it['slug']}  ({titulo} / {alt})")
        m, p, vistos = mejor([titulo, alt])
        fila = {"slug": it["slug"], "titulo": titulo, "alt": alt, "puntos": p, "candidatos": vistos}
        if m:
            fila["anilist"] = {"id": m["id"], "romaji": m["title"].get("romaji"), "english": m["title"].get("english"),
                               "formato": m.get("format"), "anio": m.get("seasonYear"), "episodios": m.get("episodes"),
                               "banner": m.get("bannerImage"), "poster": (m.get("coverImage") or {}).get("extraLarge"),
                               "descripcion": (m.get("description") or "")[:300]}
            print(f"  → {m['title'].get('romaji')} / {m['title'].get('english')} (AniList {m['id']}, {p} puntos)")
            try:
                r = requests.get(ANIZIP.format(m["id"]), timeout=30, headers={"User-Agent": "noads-diag/1"})
                print(f"  ani.zip: HTTP {r.status_code}, {len(r.text) // 1024} KB")
                z = r.json() if r.status_code == 200 else {}
                eps = z.get("episodes") or {}
                muestra = {}
                for k in list(eps)[:2]:
                    e = eps[k]
                    muestra[k] = {"title": e.get("title"), "overview": (e.get("overview") or "")[:200], "image": e.get("image"),
                                  "airDate": e.get("airDate"), "runtime": e.get("runtime")}
                fila["anizip"] = {"episodios": len(eps), "episodeCount": z.get("episodeCount"), "claves": list(eps)[:5] + list(eps)[-3:],
                                  "imagenes": z.get("images"), "mappings": z.get("mappings"), "muestra": muestra,
                                  "con_overview": sum(1 for e in eps.values() if e.get("overview")),
                                  "con_imagen": sum(1 for e in eps.values() if e.get("image")),
                                  "con_titulo_en": sum(1 for e in eps.values() if (e.get("title") or {}).get("en"))}
                print(f"  ani.zip: {len(eps)} episodios, {fila['anizip']['con_overview']} con descripción, {fila['anizip']['con_imagen']} con imagen; "
                      f"imágenes: {[i.get('coverType') for i in (z.get('images') or [])]}")
            except Exception as e:
                print(f"  ani.zip: {type(e).__name__}: {e}")
            time.sleep(0.5)
        else:
            print("  → sin coincidencia")
        salida.append(fila)
    with open(os.path.join(HERE, "anilist_out.json"), "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
