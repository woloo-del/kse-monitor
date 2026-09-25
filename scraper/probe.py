"""Przebieg diagnostyczny: zapisuje surowe próbki źródeł do out/probe/ (uruchamiany ręcznie, PROBE=1)."""
import json, os, re, io, zipfile, datetime as dt
from .common import get, now_local, OUT

PSE = "https://api.raporty.pse.pl/api/"
REPORTS = ["crb-rozl", "cmbp-tp", "cmbu-tu", "mbp-tp", "mbu-tu", "eb-rozl", "en-rozl", "poebrbn", "poebrbb", "pomb-rbn",
           "popmb-rmb", "zmb", "cor-rozl", "poze-redoze", "ogr-oper", "ogr-d", "ogr-b", "ogr-m", "pk5l-wp",
           "s_wi_pbm_p_gen_fcst", "s_pv_pbm_p_gen_prg", "przeplywymocy", "sk-d", "cen-cost"]


def _w(name, text):
    p = os.path.join(OUT, "probe")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, name), "w", encoding="utf-8") as f:
        f.write(text)


def run():
    d = now_local().date()
    summary = {}
    for rep in REPORTS:
        res = {}
        for day in (d, d - dt.timedelta(days=1), d + dt.timedelta(days=1)):
            try:
                r = get(PSE + rep, params={"$filter": f"business_date eq '{day}'", "$first": "2000"}, expect_json=True, tries=1, timeout=40)
                rows = r.get("value", [])
                res[str(day)] = {"n": len(rows), "fields": sorted(rows[0].keys()) if rows else [], "sample": rows[:3], "next": bool(r.get("nextLink"))}
            except Exception as e:  # noqa: BLE001
                res[str(day)] = {"err": str(e)[:200]}
        summary[rep] = res
    _w("pse.json", json.dumps(summary, ensure_ascii=False, indent=1))
    tge = {}
    for url in ("https://tge.pl/krzywe_zagregowane", "https://tge.pl/Krzywe_15_30_60"):
        try:
            h = get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36"}).text
            links = sorted(set(re.findall(r'href="([^"]+\.(?:zip|xml|xlsx|csv)[^"]*)"', h)))
            tge[url] = {"len": len(h), "links": links[:40], "n_links": len(links)}
            _w(re.sub(r"\W+", "_", url)[-30:] + ".html", h)
        except Exception as e:  # noqa: BLE001
            tge[url] = {"err": str(e)[:200]}
    # próbka najnowszego ZIP-a
    cand = []
    for v in tge.values():
        cand += v.get("links", [])
    cand = [c for c in cand if c.endswith(".zip")]
    for c in sorted(cand)[-2:]:
        url = c if c.startswith("http") else "https://tge.pl" + (c if c.startswith("/") else "/" + c)
        try:
            b = get(url.replace(" ", "%20"), timeout=60).content
            z = zipfile.ZipFile(io.BytesIO(b))
            names = z.namelist()
            first = z.read(names[0]).decode("utf-8", "replace")
            tge.setdefault("zips", []).append({"url": url, "size": len(b), "names": names[:10]})
            _w("zip_" + os.path.basename(c)[:60] + ".xml.txt", first[:60000])
        except Exception as e:  # noqa: BLE001
            tge.setdefault("zips", []).append({"url": url, "err": str(e)[:200]})
    _w("tge.json", json.dumps(tge, ensure_ascii=False, indent=1))
