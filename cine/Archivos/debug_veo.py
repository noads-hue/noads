import requests, sys
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "es-ES,es;q=0.9", "Referer": "https://veohentai.com/"}
for u in ["https://veohentai.com/", "https://veohentai.com/explorar/?orden=vistas", "https://veohentai.com/explorar/", "https://veohentai.com/explorar/?pagina=2", "https://veohentai.com/generos/", "https://veohentai.com/genero/hentai-ntr/"]:
    for hdrs, tag in ((H, "app"), ({"User-Agent": UA}, "soloUA"), ({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept": H["Accept"], "Accept-Language": H["Accept-Language"]}, "desktop")):
        try:
            r = requests.get(u, headers=hdrs, timeout=30)
            srv = r.headers.get("server", "?"); cf = r.headers.get("cf-mitigated", "")
            print(f"{tag:8} {r.status_code} {len(r.text):7d} server={srv} cf={cf} {u}  {r.text[:80]!r}" if r.status_code != 200 else f"{tag:8} {r.status_code} {len(r.text):7d} server={srv} {u}")
        except Exception as e:
            print(f"{tag:8} ERR {u} {e}")
