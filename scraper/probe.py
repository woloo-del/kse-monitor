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


def run2():
    """Druga runda: mapa endpointów (PDF), $metadata, raporty bez filtra daty."""
    out = {}
    for url, name in (("https://api.raporty.pse.pl/EndpointsMap.pdf", "EndpointsMap.pdf"), ("https://api.raporty.pse.pl/api/$metadata", "metadata.xml")):
        try:
            b = get(url, timeout=60).content
            p = os.path.join(OUT, "probe"); os.makedirs(p, exist_ok=True)
            open(os.path.join(p, name), "wb").write(b)
            out[name] = len(b)
        except Exception as e:  # noqa: BLE001
            out[name] = str(e)[:200]
    for rep in ["crb-rozl", "eb-rozl", "en-rozl", "cor-rozl", "cen-rozl", "ceb-rozl", "ckoeb-rozl", "ceb-pp", "cen", "sk", "sk-d",
                "gen-jw", "przeplywy-mocy", "his-bil-mocy", "ro-rozl", "poze-redoze"]:
        try:
            r = get(PSE + rep, params={"$first": "3", "$orderby": "dtime desc"}, expect_json=True, tries=1, timeout=40)
            rows = r.get("value", [])
            out[rep] = {"n": len(rows), "sample": rows[:2]}
        except Exception as e:  # noqa: BLE001
            try:
                r = get(PSE + rep, params={"$first": "3"}, expect_json=True, tries=1, timeout=40)
                out[rep] = {"n": len(r.get("value", [])), "sample": r.get("value", [])[:2], "note": "bez orderby"}
            except Exception as e2:  # noqa: BLE001
                out[rep] = {"err": str(e2)[:160]}
    _w("pse2.json", json.dumps(out, ensure_ascii=False, indent=1))


def run():
    if os.environ.get("PROBE_STAGE") == "2":
        return run2()
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
