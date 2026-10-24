import re, sys
from curl_cffi import requests as cr
B = "https://monoschinos.st"
UA_M = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
slug = "tokyo-revengers-santen-sensou-hen-sub-espanol"
def probe(tag, s, headers):
    try:
        s.get(B, headers=headers, timeout=30)
        r = s.get(f"{B}/anime/{slug}", headers=headers, timeout=30)
        h = r.text
        marks = {"h1light": bool(re.search(r'<h1[^>]*class="[^"]*text-light', h, re.I)), "data-ajax": bool(re.search(r'data-ajax="https://monoschinos\.st/ajax/ajax_pagination/(\d+)"', h, re.I)),
                 "csrf": bool(re.search(r'name="csrf-token"', h, re.I)), "p75": len(re.findall(r'class="[^"]*text-white/75', h)), "caplist": "caplist" in h, "ver-links": len(re.findall(r'/ver/', h))}
        print(f"{tag:22} {r.status_code} {len(h):7d} {marks}")
        m = re.search(r'data-ajax="https://monoschinos\.st/ajax/ajax_pagination/(\d+)"', h, re.I)
        tok = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', h, re.I)
        if m:
            aid = m.group(1); hd = dict(headers); hd.update({"Accept": "text/html, */*; q=0.01", "X-Requested-With": "XMLHttpRequest", "Origin": B, "Referer": f"{B}/anime/{slug}"})
            if tok: hd["X-CSRF-TOKEN"] = tok.group(1)
            data = {"p": "1"}; 
            if tok: data["_token"] = tok.group(1)
            r2 = s.post(f"{B}/ajax/ajax_pagination/{aid}", data=data, headers=hd, timeout=30)
            print(f"   ajax p=1: {r2.status_code} {len(r2.text)} nums={len(re.findall(r'\"num\"', r2.text))} {r2.text[:120]!r}")
            r3 = s.post(f"{B}/ajax/caplist/{aid}", data=data, headers=hd, timeout=30)
            print(f"   caplist : {r3.status_code} {len(r3.text)} caps={r3.text.count('episodio')} {r3.text[:100]!r}")
        else:
            h1 = re.search(r"<h1[^>]*>", h); print("   sin data-ajax; h1:", h1.group(0)[:120] if h1 else None, "| ajax_pagination en html:", "ajax_pagination" in h, "| sample:", re.findall(r'data-ajax="[^"]*"', h)[:2])
    except Exception as e:
        print(f"{tag:22} ERR {e}")
base_h = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.9", "Accept-Language": "es-ES,es;q=0.9"}
probe("chrome", cr.Session(impersonate="chrome"), base_h)
probe("chrome+UA movil", cr.Session(impersonate="chrome"), {**base_h, "User-Agent": UA_M})
for imp in ("chrome_android", "chrome131_android", "chrome99_android"):
    try:
        probe(imp, cr.Session(impersonate=imp), base_h)
    except Exception as e:
        print(imp, "no disponible:", e)
