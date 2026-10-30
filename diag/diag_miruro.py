#!/usr/bin/env python3
"""Diagnóstico: ¿se puede leer Miruro desde GitHub Actions? Guarda las páginas en diag/ para estudiar su formato."""
import json, os, re, sys, time
from urllib.parse import quote

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None
import requests

BASE = "https://miruro.to"
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
HEAD = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "es-419,es;q=0.9,en;q=0.8"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def get(url):
    """(modo, código, html). Prueba curl_cffi (huella de Chrome) y luego requests con UA móvil."""
    if curl_requests is not None:
        try:
            r = curl_requests.get(url, headers=HEAD, impersonate="chrome", timeout=40)
            print(f"  curl_cffi: HTTP {r.status_code}, {len(r.text) // 1024} KB")
            if r.status_code == 200 and "Just a moment" not in r.text:
                return "curl_cffi", r.status_code, r.text
        except Exception as e:
            print(f"  curl_cffi: {type(e).__name__}: {e}")
    try:
        r = requests.get(url, headers={**HEAD, "User-Agent": UA}, timeout=40)
        print(f"  requests: HTTP {r.status_code}, {len(r.text) // 1024} KB")
        return "requests", r.status_code, r.text
    except Exception as e:
        print(f"  requests: {type(e).__name__}: {e}")
        return "ninguno", 0, ""


def guardar(nombre, texto):
    with open(os.path.join(OUT, nombre), "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"  guardado {nombre} ({len(texto) // 1024} KB)")


def main():
    resumen = {}
    for i, q in enumerate(["Tsuihou sareta tensei juukishi wa game chishiki de musou suru", "Sousou no Frieren"]):
        url = f"{BASE}/search?q=" + quote(q)
        print(f"GET {url}")
        modo, code, html = get(url)
        resumen[f"search{i}"] = {"modo": modo, "code": code, "kb": len(html) // 1024, "sveltekit": "__sveltekit" in html,
                                 "watch_links": len(set(re.findall(r"/watch/([A-Za-z0-9_-]{8,})/", html)))}
        guardar(f"miruro_search{i}.html", html)
        time.sleep(2)
    # la primera serie que aparezca en la última búsqueda
    m = re.search(r"/(?:watch|info)/([A-Za-z0-9_-]{8,})/([a-z0-9][a-z0-9-]*)", html)
    if m:
        wid, slug = m.group(1), m.group(2)
        for ep in (None, 1):
            url = f"{BASE}/watch/{wid}/{slug}" + (f"?ep={ep}" if ep else "")
            print(f"GET {url}")
            modo, code, html2 = get(url)
            resumen[f"watch{ep or 0}"] = {"modo": modo, "code": code, "kb": len(html2) // 1024, "sveltekit": "__sveltekit" in html2,
                                        "episodesData": "episodesData" in html2, "sourcesData": "sourcesData" in html2}
            guardar(f"miruro_watch{ep or 0}.html", html2)
            time.sleep(2)
    print(json.dumps(resumen, indent=1))
    guardar("miruro_resumen.json", json.dumps(resumen, indent=1))


if __name__ == "__main__":
    main()
