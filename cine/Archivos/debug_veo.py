import sys, time
U = ["https://veohentai.com/explorar/", "https://veohentai.com/generos/"]
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "es-ES,es;q=0.9", "Referer": "https://veohentai.com/"}
import requests
for u in U:
    r = requests.get(u, headers=H, timeout=30); print(f"requests          {r.status_code} cf={r.headers.get('cf-mitigated','')} {u}")
try:
    from curl_cffi import requests as cr
    for imp in ("chrome", "chrome124", "chrome110", "safari17_0", "edge101"):
        for u in U:
            try:
                r = cr.get(u, impersonate=imp, timeout=30)
                print(f"curl_cffi {imp:10} {r.status_code} cf={r.headers.get('cf-mitigated','')} {u} len={len(r.text)}")
            except Exception as e:
                print(f"curl_cffi {imp:10} ERR {u} {type(e).__name__}: {str(e)[:80]}")
    # con los headers de la app + impersonate
    s = cr.Session(impersonate="chrome")
    r = s.get(U[0], headers={"Accept-Language": "es-ES,es;q=0.9", "Referer": "https://veohentai.com/"}, timeout=30)
    print(f"curl_cffi sesión+headers {r.status_code} cf={r.headers.get('cf-mitigated','')} len={len(r.text)} initial={'\"initial\":[' in r.text}")
    r2 = s.get("https://veohentai.com/explorar/?pagina=2", timeout=30); print(f"curl_cffi sesión pág2 {r2.status_code} initial={'\"initial\":[' in r2.text}")
except Exception as e:
    print("curl_cffi no disponible:", e)
