import json
import re
import sys
import time

from curl_cffi import requests as cr

try:
    from curl_cffi import CurlHttpVersion
except Exception:  # noqa
    CurlHttpVersion = None

B = "https://monoschinos.st"
SLUG = "tokyo-revengers-santen-sensou-hen-sub-espanol"
PAGE = f"{B}/anime/{SLUG}"
ECHO = "https://httpbin.org/anything"
UA_APP = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/120.0.0.0 Mobile Safari/537.36")


def short(t, n=60):
    return re.sub(r"\s+", " ", t[:n])


def page_info(s, headers=None, **kw):
    r = s.get(PAGE, headers=headers or {}, timeout=30, **kw)
    h = r.text
    m = re.search(r'data-ajax="https://monoschinos\.st/ajax/ajax_pagination/(\d+)"', h, re.I)
    tok = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', h, re.I)
    cookies = sorted(k for k in s.cookies.keys())
    print(f"   GET ficha: {r.status_code} {len(h)}B ajax_id={m.group(1) if m else None} csrf={'sí' if tok else 'no'} cookies={cookies}")
    return (m.group(1) if m else None), (tok.group(1) if tok else None), r


def do_post(tag, s, aid, csrf, headers, method="POST", **kw):
    url = f"{B}/ajax/ajax_pagination/{aid}"
    data = {"p": "1"}
    if csrf:
        data["_token"] = csrf
    try:
        if method == "POST":
            r = s.post(url, data=data, headers=headers, timeout=30, **kw)
        else:
            r = s.get(url + "?p=1", headers=headers, timeout=30, **kw)
        cf = r.headers.get("cf-mitigated", "-")
        print(f"   {tag:38} -> {r.status_code} cf-mitigated={cf} len={len(r.text)} nums={r.text.count(chr(34) + 'num' + chr(34))} | {short(r.text)!r}")
        return r
    except Exception as e:
        print(f"   {tag:38} -> ERR {e}")
        return None


def xhr_headers(ua, chua=None, chua_mobile=None, chua_platform=None, csrf=None, accept="*/*", origin=True, xrw=True,
                priority=True):
    h = {}
    if chua_platform:
        h["sec-ch-ua-platform"] = chua_platform
    if csrf:
        h["x-csrf-token"] = csrf
    h["user-agent"] = ua
    if chua:
        h["sec-ch-ua"] = chua
    h["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if xrw:
        h["x-requested-with"] = "XMLHttpRequest"
    if chua_mobile:
        h["sec-ch-ua-mobile"] = chua_mobile
    h["accept"] = accept
    if origin:
        h["origin"] = B
    h["sec-fetch-site"] = "same-origin"
    h["sec-fetch-mode"] = "cors"
    h["sec-fetch-dest"] = "empty"
    h["referer"] = PAGE
    h["accept-encoding"] = "gzip, deflate, br, zstd"
    h["accept-language"] = "es-ES,es;q=0.9"
    if priority:
        h["priority"] = "u=1, i"
    return h


def defaults_of(imp):
    """Cabeceras por defecto que manda curl_cffi con esa huella (vía httpbin)."""
    try:
        s = cr.Session(impersonate=imp)
        r = s.get(ECHO, timeout=30)
        hd = {k.lower(): v for k, v in r.json().get("headers", {}).items()}
        return hd
    except Exception as e:
        print(f"   (httpbin falló para {imp}: {e})")
        return {}


def main():
    print("curl_cffi", cr.__version__ if hasattr(cr, "__version__") else "?")
    # 0) qué manda curl_cffi por defecto con chrome
    d = defaults_of("chrome")
    print("0) defaults chrome:", json.dumps({k: d.get(k) for k in ("user-agent", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "accept", "accept-language", "sec-fetch-mode", "upgrade-insecure-requests", "priority")}))
    ua = d.get("user-agent", UA_APP)
    chua, chm, chp = d.get("sec-ch-ua"), d.get("sec-ch-ua-mobile"), d.get("sec-ch-ua-platform")

    # 1) baseline: chrome + cabeceras actuales de post_ajax
    print("1) chrome + cabeceras de post_ajax (lo que hace el script)")
    s = cr.Session(impersonate="chrome")
    aid, csrf, _ = page_info(s)
    base = {"Accept": "text/html, */*; q=0.01", "Accept-Language": "es-ES,es;q=0.9", "Referer": PAGE,
            "X-Requested-With": "XMLHttpRequest", "Origin": B, "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin", "X-CSRF-TOKEN": csrf or ""}
    do_post("POST baseline", s, aid, csrf, base)
    do_post("POST baseline sin Origin", s, aid, csrf, {k: v for k, v in base.items() if k != "Origin"})
    do_post("GET ?p=1 (cabeceras ajax)", s, aid, csrf, base, method="GET")
    do_post("GET ?p=1 (navegación)", s, aid, csrf, {}, method="GET")
    # ¿qué manda realmente esa POST?
    try:
        r = s.post(ECHO, data={"p": "1"}, headers=base, timeout=30)
        print("   enviado (baseline):", json.dumps(r.json().get("headers", {}), ensure_ascii=False)[:900])
    except Exception as e:
        print("   httpbin:", e)

    # 2) chrome con default_headers=False y cabeceras XHR completas (orden Chrome)
    print("2) chrome, default_headers=False, XHR calcado")
    s = cr.Session(impersonate="chrome", default_headers=False)
    nav = {"sec-ch-ua": chua or "", "sec-ch-ua-mobile": chm or "?0", "sec-ch-ua-platform": chp or '"Windows"',
           "upgrade-insecure-requests": "1", "user-agent": ua,
           "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
           "sec-fetch-site": "none", "sec-fetch-mode": "navigate", "sec-fetch-user": "?1", "sec-fetch-dest": "document",
           "accept-encoding": "gzip, deflate, br, zstd", "accept-language": "es-ES,es;q=0.9", "priority": "u=0, i"}
    nav = {k: v for k, v in nav.items() if v}
    aid, csrf, _ = page_info(s, nav)
    hx = xhr_headers(ua, chua, chm, chp, csrf)
    do_post("POST XHR calcado accept */*", s, aid, csrf, hx)
    do_post("POST XHR accept text/html", s, aid, csrf, xhr_headers(ua, chua, chm, chp, csrf, accept="text/html, */*; q=0.01"))
    do_post("POST XHR sin priority", s, aid, csrf, xhr_headers(ua, chua, chm, chp, csrf, priority=False))
    do_post("POST XHR sin Origin", s, aid, csrf, xhr_headers(ua, chua, chm, chp, csrf, origin=False))
    do_post("POST XHR sin X-Requested-With", s, aid, csrf, xhr_headers(ua, chua, chm, chp, csrf, xrw=False))
    if CurlHttpVersion is not None:
        do_post("POST XHR HTTP/1.1", s, aid, csrf, hx, http_version=CurlHttpVersion.V1_1)
    try:
        r = s.post(ECHO, data={"p": "1"}, headers=hx, timeout=30)
        print("   enviado (XHR calcado):", json.dumps(r.json().get("headers", {}), ensure_ascii=False)[:900])
    except Exception as e:
        print("   httpbin:", e)
    time.sleep(2)

    # 3) otras huellas
    for imp in ("safari17_0", "safari18_0", "safari17_2_ios", "firefox", "firefox135", "chrome_android", "edge101", "chrome110", "chrome99"):
        print(f"3) huella {imp}")
        try:
            s = cr.Session(impersonate=imp)
        except Exception as e:
            print("   no disponible:", e)
            continue
        try:
            aid, csrf, _ = page_info(s)
        except Exception as e:
            print("   GET ficha falló:", e)
            continue
        dd = defaults_of(imp)
        hx = xhr_headers(dd.get("user-agent", ua), dd.get("sec-ch-ua"), dd.get("sec-ch-ua-mobile"), dd.get("sec-ch-ua-platform"), csrf,
                         priority="priority" in dd)
        do_post("POST XHR calcado", s, aid, csrf, hx)
        do_post("POST cabeceras post_ajax", s, aid, csrf, dict(base, **({"X-CSRF-TOKEN": csrf} if csrf else {})))
        time.sleep(1)

    # 4) sin huella (curl pelado), UA de la app, HTTP/1.1
    print("4) sin impersonate, UA de la app")
    try:
        s = cr.Session()
        aid, csrf, r = page_info(s, {"User-Agent": UA_APP, "Accept-Language": "es-ES,es;q=0.9"})
        if aid:
            do_post("POST cabeceras post_ajax", s, aid, csrf, dict(base, **{"User-Agent": UA_APP, "X-CSRF-TOKEN": csrf or ""}))
            if CurlHttpVersion is not None:
                do_post("POST post_ajax HTTP/1.1", s, aid, csrf, dict(base, **{"User-Agent": UA_APP, "X-CSRF-TOKEN": csrf or ""}), http_version=CurlHttpVersion.V1_1)
        else:
            print("   ficha:", r.status_code, short(r.text, 80))
    except Exception as e:
        print("   ERR", e)

    # 5) ¿la página del episodio trae la lista completa?
    print("5) página del episodio 1")
    try:
        s = cr.Session(impersonate="chrome")
        r = s.get(f"{B}/ver/{SLUG.replace('-sub-espanol', '')}-episodio-1", headers={"Accept-Language": "es-ES,es;q=0.9"}, timeout=30)
        h = r.text
        links = re.findall(r'href="(https://monoschinos\.st/ver/[^"]+)"', h)
        uniq = sorted(set(links), key=lambda u: int(re.search(r"episodio-(\d+)", u).group(1)) if re.search(r"episodio-(\d+)", u) else 0)
        print(f"   {r.status_code} {len(h)}B /ver/ links={len(links)} únicos={len(uniq)} select={'<select' in h} option={h.count('<option')} data-ajax={'data-ajax' in h} caplist={'caplist' in h}")
        print("   primeros:", uniq[:3], "últimos:", uniq[-3:])
        for mark in ("episodio-2", "lista", "Lista", "caplist", "ajax"):
            i = h.find(mark)
            if i >= 0:
                print(f"   ...{mark}: {short(h[max(0, i - 120): i + 160], 280)!r}")
    except Exception as e:
        print("   ERR", e)

    # 6) sitemap
    print("6) sitemap")
    try:
        s = cr.Session(impersonate="chrome")
        for u in (f"{B}/sitemap.xml", f"{B}/sitemap_index.xml", f"{B}/robots.txt"):
            r = s.get(u, timeout=30)
            print(f"   {u} -> {r.status_code} {len(r.text)}B {short(r.text, 200)!r}")
    except Exception as e:
        print("   ERR", e)


main()
