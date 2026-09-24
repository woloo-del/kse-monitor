"""Awarie i wyłączenia OSD: Energa-Operator, Enea Operator, PGE Dystrybucja, Tauron Dystrybucja.
Stoen Operator nie publikuje danych poza aplikacją JS – pomijany (status: brak źródła)."""
import re, json, html, hashlib, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .common import get, now_local, iso, TZ, UTC, read_prev, write
from . import geo

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "pl,en;q=0.9"}
HORIZON_H = 36
MONTHS = {"stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
          "września": 9, "października": 10, "listopada": 11, "grudnia": 12}


def _local(s):
    """Naiwna data 'YYYY-MM-DD HH:MM[:SS]' lub ISO -> datetime w strefie Warszawy (jeśli bez strefy)."""
    if not s:
        return None
    s = s.strip().replace(" ", "T", 1)
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _keep(ev, now):
    s, e = ev.get("_s"), ev.get("_e")
    if e and e < now - dt.timedelta(minutes=30):
        return False
    if s and s > now + dt.timedelta(hours=HORIZON_H):
        return False
    return True


def _finish(ev, now):
    s, e = ev.pop("_s", None), ev.pop("_e", None)
    ev["start"], ev["end"] = iso(s), iso(e)
    ev["active"] = bool((s is None or s <= now) and (e is None or e >= now))
    if ev.get("lat") is not None and not ev.get("woj"):
        ev["woj"] = geo.woj_of(ev["lat"], ev["lon"])
    ev["desc"] = (ev.get("desc") or "")[:240]
    ev["place"] = (ev.get("place") or "")[:120]
    return ev


def _short_place(text):
    parts = [p.split(":")[0].strip() for p in (text or "").split(";") if p.strip()]
    head = ", ".join(parts[:3])
    return (head + (f" i {len(parts) - 3} innych" if len(parts) > 3 else ""))[:120]


def _count_addr(text):
    if not text:
        return None
    return max(1, len(re.findall(r"\d+[a-zA-Z]?(?:\s*[-–]\s*\d+[a-zA-Z]?)?", text)))


# ---------------- Energa-Operator ----------------
ENERGA = "https://energa-operator.pl"


def energa():
    page = get(ENERGA + "/uslugi/awarie-i-wylaczenia/wylaczenia-planowane", headers=BROWSER).text
    soup = BeautifulSoup(page, "html.parser")
    div = soup.find("div", attrs={"data-shutdowns": True})
    if not div:
        raise RuntimeError("brak atrybutu data-shutdowns na stronie Energa")
    data = get(urljoin(ENERGA, div["data-shutdowns"]), headers={**BROWSER, "Accept": "application/json"}, expect_json=True)
    out = []
    for s in (data.get("document", {}).get("payload", {}).get("shutdowns") or []):
        lat = lon = None
        poly = s.get("polygon") or {}
        c = (poly.get("centroid") or {}).get("coordinates") if isinstance(poly, dict) else None
        if c and len(c) >= 2:
            lon, lat = float(c[0]), float(c[1])
        areas = s.get("areas") or []
        place = ", ".join(areas[:3]) or s.get("regionName") or ""
        approx = False
        if lat is None and areas:
            g = geo.gmina_centroid(re.split(r"\s+(obszar|miasto|gmina)", areas[0])[0])
            if g:
                lat, lon, _, _ = g
                approx = True
        out.append({"id": "energa:" + str(s.get("guid")), "operator": "Energa-Operator",
                    "type": "awaria" if str(s.get("shutdownType")) == "1" else "planowane",
                    "place": place, "desc": html.unescape(s.get("message") or ""), "region": s.get("regionName") or s.get("deptName"),
                    "_s": _local(s.get("startDate")), "_e": _local(s.get("endDate")), "lat": lat, "lon": lon, "approx": approx,
                    "geo": "gmina" if approx else ("obszar" if lat is not None else None),
                    "scale": _count_addr(s.get("message")), "scale_unit": "adresów"})
    return out


# ---------------- Enea Operator ----------------
ENEA = "https://wylaczenia.operator.enea.pl/index.php"
ENEA_WOJ = {"Poznań": "wielkopolskie", "Bydgoszcz": "kujawsko-pomorskie", "Szczecin": "zachodniopomorskie",
            "Zielona Góra": "lubuskie", "Gorzów Wlkp.": "lubuskie"}
RE_PLAN = re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})\s+r\.\s+w\s+godz\.\s+(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")
RE_FAIL = re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})\s+r\.\s+do\s+godziny\s+(\d{1,2}):(\d{2})")


def _enea_regions():
    soup = BeautifulSoup(get(ENEA, params={"page": "awarie"}, headers=BROWSER).text, "html.parser")
    sel = soup.find("select", {"id": "oddzial"})
    vals = [o.get("value") for o in (sel.find_all("option") if sel else []) if o.get("value")]
    return vals or list(ENEA_WOJ)


def _enea_geo(desc, title, woj):
    m = re.search(r"miejscowo(?:ść|sci|ści)\s+([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+)?)", desc)
    g = re.search(r"Gmin(?:a|y)\s+([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+)?)", desc)
    gm = g.group(1) if g else None
    if m:
        r = geo.locality(m.group(1), woj=woj, gmina=gm)
        if r:
            return r
    first = re.match(r"\s*([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźż\-]+)?)", desc or "")
    if first:
        r = geo.locality(first.group(1), woj=woj, gmina=gm)
        if r:
            return r
    for cand in (gm, re.sub(r"^Obszar\s+", "", title or "").strip()):
        if cand:
            r = geo.gmina_centroid(cand, woj) or geo.locality(cand, woj=woj)
            if r:
                return r[0], r[1], r[2], "gmina"
    return None


ENEA_DEBUG = {}


def enea():
    out, seen = [], set()
    regions = _enea_regions()
    ENEA_DEBUG["regions"] = regions
    for region in regions:
        woj = ENEA_WOJ.get(region)
        for page, typ in (("awarie", "awaria"), ("", "planowane")):
            params = {"page": page, "oddzial": region} if page else {"oddzial": region}
            resp = get(ENEA, params=params, headers=BROWSER)
            soup = BeautifulSoup(resp.text, "html.parser")
            blocks = soup.select("div.unpl.block.info")
            dbg = {"blocks": len(blocks)}
            if not page:
                hits = [t for t in soup.find_all(string=re.compile(r"^\s*Obszar\s"))][:3]
                dbg["obszar"] = [[(p.name, " ".join(p.get("class") or [])) for p in [h.parent] + list(h.parent.parents)[:4]] for h in hits]
                dbg["n_obszar"] = len(soup.find_all(string=re.compile(r"^\s*Obszar\s")))
                if hits:
                    blk = list(hits[0].parent.parents)[1]
                    dbg["sample"] = str(blk)[:1200]
            ENEA_DEBUG[f"{region}/{page or 'planowane'}"] = dbg
            for i, b in enumerate(blocks):
                title = (b.find("h4", {"class": "title_"}) or {}).get_text(" ", strip=True) if b.find("h4", {"class": "title_"}) else ""
                desc = b.find("p", {"class": "description"}).get_text(" ", strip=True) if b.find("p", {"class": "description"}) else ""
                when = b.find("p", {"class": "bold subtext"}).get_text(" ", strip=True) if b.find("p", {"class": "bold subtext"}) else ""
                if "ODWOŁANE" in (title + when + desc).upper():
                    continue
                s = e = None
                mp, mf = RE_PLAN.search(when), RE_FAIL.search(when)
                if mp:
                    d, mon, y, h1, m1, h2, m2 = mp.groups()
                    day = dt.date(int(y), MONTHS.get(mon.lower(), 1), int(d))
                    s = dt.datetime(day.year, day.month, day.day, int(h1), int(m1), tzinfo=TZ)
                    e = dt.datetime(day.year, day.month, day.day, int(h2), int(m2), tzinfo=TZ)
                elif mf:
                    d, mon, y, h, m = mf.groups()
                    e = dt.datetime(int(y), MONTHS.get(mon.lower(), 1), int(d), int(h), int(m), tzinfo=TZ)
                key = hashlib.md5((title + desc + when).encode()).hexdigest()
                if key in seen:
                    continue
                seen.add(key)
                # typ wg formatu daty: przedział godzin = planowane, samo "do godziny" = awaria
                typ = "planowane" if mp else ("awaria" if mf else typ)
                g = _enea_geo(desc, title, woj)
                ev = {"id": f"enea:{hashlib.md5((region + page + title + desc + when).encode()).hexdigest()[:12]}", "operator": "Enea Operator", "type": typ,
                      "place": re.sub(r"^Obszar\s+", "", title), "desc": desc, "region": region,
                      "_s": s, "_e": e, "woj": woj, "scale": _count_addr(desc), "scale_unit": "adresów"}
                if g:
                    ev.update({"lat": g[0], "lon": g[1], "woj": g[2] or woj, "approx": g[3] == "gmina", "geo": g[3]})
                out.append(ev)
    return out


# ---------------- PGE Dystrybucja ----------------
PGE = "https://power-outage.gkpge.pl/api/power-outage"
PGE_HDR = {"Accept": "application/json, text/plain, */*"}


def _pge_region(rid):
    items = get(PGE, params={"regionIdentifier": rid, "page": 0, "size": 500}, headers=PGE_HDR, timeout=40, expect_json=True)
    return items if isinstance(items, list) else items.get("content") or items.get("items") or []


def pge():
    with ThreadPoolExecutor(4) as ex:
        chunks = list(ex.map(lambda r: _safe(_pge_region, r), range(1, 71)))
    out, seen = [], set()
    for items in chunks:
        for it in items or []:
            if it.get("revoked") or it.get("deleted") or it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            coords = it.get("coordinates") or []
            lat = lon = None
            pts = [(c.get("latitude"), c.get("longitude")) for c in coords if isinstance(c, dict) and c.get("latitude")]
            if pts:
                lat = sum(p[0] for p in pts) / len(pts)
                lon = sum(p[1] for p in pts) / len(pts)
            addrs = it.get("addresses") or []
            t0 = (addrs[0].get("teryt") or {}) if addrs else {}
            approx = False
            if lat is None and t0:
                r = geo.locality(t0.get("cityName") or "", woj=(t0.get("voivodeshipName") or "").lower() or None, gmina=t0.get("communeName"))
                r = r or geo.gmina_centroid(t0.get("communeName") or "", (t0.get("voivodeshipName") or "").lower() or None)
                if r:
                    lat, lon, approx = r[0], r[1], r[3] == "gmina"
            n = sum(len([x for x in (a.get("numbers") or "").split(",") if x.strip()]) or 1 for a in addrs) or None
            out.append({"id": f"pge:{it.get('id')}", "operator": "PGE Dystrybucja",
                        "type": "awaria" if it.get("type") == 1 else "planowane",
                        "place": _short_place(it.get("description") or t0.get("cityName") or it.get("regionName") or ""), "desc": it.get("description") or "",
                        "region": it.get("regionName"), "_s": _local(it.get("startAt")), "_e": _local(it.get("stopAt")),
                        "lat": lat, "lon": lon, "approx": approx, "geo": "obszar" if pts else ("gmina" if approx else "miejscowosc" if lat else None),
                        "woj": (t0.get("voivodeshipName") or "").lower() or None, "scale": n, "scale_unit": "adresów"})
    if not any(chunks):
        raise RuntimeError("PGE: żaden rejon nie odpowiedział")
    return out


# ---------------- Tauron Dystrybucja ----------------
TAURON = "https://www.tauron-dystrybucja.pl/waapi"
TAURON_WOJ = ["śląskie", "małopolskie", "opolskie", "dolnośląskie", "podkarpackie", "świętokrzyskie"]


def _tauron_districts():
    cache = read_prev("cache_tauron.json")
    if cache and len(cache.get("districts", [])) > 40:
        write("cache_tauron.json", cache)
        return cache["districts"]
    by_name, _ = geo._gaz()
    seeds = {}
    for recs in by_name.values():
        for r in recs:
            if r["woj"] in TAURON_WOJ and (r["woj"], r["pow"]) not in seeds and r["city"]:
                seeds[(r["woj"], r["pow"])] = r["name"]
    for recs in by_name.values():  # powiaty bez miasta
        for r in recs:
            if r["woj"] in TAURON_WOJ and (r["woj"], r["pow"]) not in seeds:
                seeds[(r["woj"], r["pow"])] = r["name"]
    found = {}

    def q(name):
        try:
            return get(TAURON + "/enum/geo/cities", params={"partName": name}, headers=BROWSER, expect_json=True) or []
        except Exception:  # noqa: BLE001
            return []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(q, sorted(set(seeds.values()))):
            for c in res:
                if c.get("DistrictGAID") and c.get("ProvinceGAID"):
                    found[(c["ProvinceGAID"], c["DistrictGAID"])] = c.get("DistrictName")
    districts = [{"p": p, "d": d, "name": n} for (p, d), n in sorted(found.items())]
    write("cache_tauron.json", {"districts": districts, "built": iso(now_local())})
    return districts


def tauron():
    districts = _tauron_districts()
    now = now_local()
    frm = (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    to = (now + dt.timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")

    def q(dd):
        return _safe(lambda: get(TAURON + "/outages/area", params={"provinceGAID": dd["p"], "districtGAID": dd["d"], "fromDate": frm, "toDate": to},
                                 headers=BROWSER, expect_json=True))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(q, districts))
    out, seen = [], set()
    for dd, r in zip(districts, res):
        for it in (r or {}).get("OutageItems") or []:
            oid = it.get("OutageId")
            if oid in seen:
                continue
            seen.add(oid)
            c = it.get("Center") or {}
            ap = it.get("AddressPointIds")
            out.append({"id": f"tauron:{oid}", "operator": "Tauron Dystrybucja",
                        "type": "awaria" if it.get("TypeId") == 2 else "planowane",
                        "place": (it.get("Message") or "")[:120], "desc": it.get("Message") or "", "region": dd.get("name"),
                        "_s": _local(it.get("StartDate")), "_e": _local(it.get("EndDate")),
                        "lat": c.get("lat"), "lon": c.get("lng"), "approx": False, "geo": "obszar" if c else None,
                        "scale": len(ap) if isinstance(ap, list) and ap else None, "scale_unit": "adresów"})
    if not any(res):
        raise RuntimeError("Tauron: żaden powiat nie odpowiedział")
    return out


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def run(status, heavy=True):
    now = now_local()
    events, per = [], {}
    sources = [("Energa-Operator", energa), ("Enea Operator", enea)]
    if heavy:
        sources += [("PGE Dystrybucja", pge), ("Tauron Dystrybucja", tauron)]
    prev = (read_prev("events.json") or {}).get("events", [])
    for name, fn in sources:
        try:
            evs = [e for e in fn() if _keep(e, now)]
            events += [_finish(e, now) for e in evs]
            per[name] = {"ok": True, "n": len(evs), "at": iso(now)}
        except Exception as e:  # noqa: BLE001
            per[name] = {"ok": False, "n": 0, "at": iso(now), "msg": str(e)[:160]}
            events += [x for x in prev if x.get("operator") == name]  # ostatni udany odczyt
    if not heavy:  # PGE i Tauron co drugi przebieg – przenieś poprzedni odczyt
        for name in ("PGE Dystrybucja", "Tauron Dystrybucja"):
            old = [x for x in prev if x.get("operator") == name]
            events += old
            pst = (read_prev("status.json") or {}).get("osd_detail", {}).get(name)
            per[name] = pst or {"ok": bool(old), "n": len(old), "at": None}
    per["Stoen Operator"] = {"ok": False, "n": 0, "at": None, "msg": "brak publicznego źródła danych (tylko aplikacja JS)"}
    ok_n = sum(1 for v in per.values() if v.get("ok"))
    status["osd"] = {"ok": ok_n > 0, "at": iso(now), "msg": f"{ok_n}/5 operatorów, {sum(1 for e in events if e['type']=='awaria')} awarii, "
                     f"{sum(1 for e in events if e['type']=='planowane')} wyłączeń planowanych (okno {HORIZON_H} h)"}
    status["osd_detail"] = per
    status.setdefault("debug", {})["enea"] = ENEA_DEBUG
    return events
