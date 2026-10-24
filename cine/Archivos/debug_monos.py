import json
import re

from curl_cffi import requests as cr

B = "https://monoschinos.st"
H = {"Accept-Language": "es-ES,es;q=0.9"}
AJ = {"Accept": "text/html, */*; q=0.01", "Accept-Language": "es-ES,es;q=0.9", "X-Requested-With": "XMLHttpRequest",
      "Origin": B, "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}


def short(t, n=300):
    return re.sub(r"\s+", " ", t[:n])


def ficha(s, slug, save=None):
    r = s.get(f"{B}/anime/{slug}", headers=H, timeout=30)
    h = r.text
    if save:
        open(save, "w", encoding="utf-8").write(h)
    m = re.search(r'data-ajax="https://monoschinos\.st/ajax/ajax_pagination/(\d+)"', h, re.I)
    tok = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', h, re.I)
    print(f"{slug}: GET {r.status_code} {len(h)}B ajax_id={m.group(1) if m else None}")
    return (m.group(1) if m else None), (tok.group(1) if tok else None)


def g(s, url, referer, csrf):
    hd = dict(AJ, Referer=referer)
    if csrf:
        hd["X-CSRF-TOKEN"] = csrf
    r = s.get(url, headers=hd, timeout=30)
    return r.status_code, r.text


def nums(t):
    return [int(x) for x in re.findall(r'"num"\s*:\s*(\d+)', t)]


s = cr.Session(impersonate="chrome")
s.get(B, headers=H, timeout=30)

slug = "tokyo-revengers-santen-sensou-hen-sub-espanol"
aid, csrf = ficha(s, slug, save="cine/Archivos/debug_ficha.html")
ref = f"{B}/anime/{slug}"
st, t = g(s, f"{B}/ajax/ajax_pagination/{aid}?p=1", ref, csrf)
print(" ajax_pagination p=1:", st, repr(t[:600]))
st, t = g(s, f"{B}/ajax/caplist/{aid}?p=1", ref, csrf)
print(" caplist p=1:", st, len(t), repr(t[:500]))
try:
    j = json.loads(t)
    caps = j.get("caps")
    print("  keys:", list(j.keys()), "| caps:", len(caps) if isinstance(caps, list) else caps, "| cap[0]:", caps[0] if caps else None)
except Exception as e:
    print("  no JSON:", e)
r = s.get(f"{B}/ver/tokyo-revengers-santen-sensou-hen-episodio-1", headers=H, timeout=30)
open("cine/Archivos/debug_ep.html", "w", encoding="utf-8").write(r.text)
print(" episodio 1 guardado:", r.status_code, len(r.text))

for slug in ("naruto-shippuden-sub-espanol", "boruto-naruto-next-generations-sub-espanol"):
    aid, csrf = ficha(s, slug)
    if not aid:
        continue
    ref = f"{B}/anime/{slug}"
    for p in (1, 2, 3, 11):
        st, t = g(s, f"{B}/ajax/ajax_pagination/{aid}?p={p}", ref, csrf)
        n = nums(t)
        pm = re.search(r'"perpage"\s*:\s*(\d+)', t)
        print(f" ajax_pagination p={p}: {st} {len(t)}B nums={len(n)} primero={n[:1]} último={n[-1:]} perpage={pm.group(1) if pm else None}")
    for p in (1, 2):
        st, t = g(s, f"{B}/ajax/caplist/{aid}?p={p}", ref, csrf)
        try:
            caps = json.loads(t).get("caps")
            eps = [c.get("episodio") for c in caps] if isinstance(caps, list) else None
            print(f" caplist p={p}: {st} {len(t)}B caps={len(caps) if isinstance(caps, list) else caps} eps {eps[:3]}…{eps[-3:] if eps else None}")
        except Exception as e:
            print(f" caplist p={p}: {st} {len(t)}B no JSON: {e} {short(t, 120)!r}")
