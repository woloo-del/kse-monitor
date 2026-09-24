"""Geokodowanie: rejestr miejscowości PRNG (jjbartek/polskie-miejscowosci, CC BY) + granice województw."""
import json, os, re, unicodedata
from functools import lru_cache

BASE = os.path.join(os.path.dirname(__file__), "..", "geo")


def norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"[\s\-–]+", " ", s)
    return s


def strip_pl(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).replace("ł", "l")


@lru_cache(maxsize=1)
def _gaz():
    rows = json.load(open(os.path.join(BASE, "miejscowosci.json"), encoding="utf-8"))
    by_name, by_gmina = {}, {}
    for name, woj, pow_, gm, lat, lon, typ in rows:
        rec = {"name": name, "woj": woj, "pow": pow_, "gmina": gm, "lat": lat, "lon": lon, "city": typ == "c"}
        by_name.setdefault(norm(name), []).append(rec)
        if strip_pl(norm(name)) != norm(name):
            by_name.setdefault(strip_pl(norm(name)), []).append(rec)
        g = norm(gm.split("-gmina")[0])
        by_gmina.setdefault(g, []).append(rec)
    cent = {}
    for g, recs in by_gmina.items():
        # centroid gminy = średnia współrzędnych jej miejscowości, osobno dla każdego województwa
        grp = {}
        for r in recs:
            grp.setdefault(r["woj"], []).append(r)
        cent[g] = [{"woj": w, "lat": sum(x["lat"] for x in rs) / len(rs), "lon": sum(x["lon"] for x in rs) / len(rs), "n": len(rs)}
                   for w, rs in grp.items()]
    return by_name, cent


def locality(name, woj=None, gmina=None, powiat=None):
    """Zwraca (lat, lon, woj, dokładność) albo None. dokładność: 'miejscowosc' | 'gmina'."""
    by_name, cent = _gaz()
    cands = by_name.get(norm(name)) or by_name.get(strip_pl(norm(name))) or []
    if woj:
        c2 = [c for c in cands if c["woj"] == woj]
        cands = c2 or cands
    if gmina:
        c2 = [c for c in cands if norm(c["gmina"]).startswith(norm(gmina))]
        cands = c2 or cands
    if powiat:
        c2 = [c for c in cands if norm(powiat) in norm(c["pow"])]
        cands = c2 or cands
    if len(cands) == 1 or (cands and (woj or gmina)):
        c = sorted(cands, key=lambda x: not x["city"])[0]
        return c["lat"], c["lon"], c["woj"], "miejscowosc"
    if cands:
        cities = [c for c in cands if c["city"]]
        if len(cities) == 1:
            c = cities[0]
            return c["lat"], c["lon"], c["woj"], "miejscowosc"
    return None


def gmina_centroid(gmina, woj=None):
    _, cent = _gaz()
    lst = cent.get(norm(gmina)) or []
    if woj:
        lst = [c for c in lst if c["woj"] == woj] or lst
    if len(lst) >= 1:
        c = max(lst, key=lambda x: x["n"])
        return c["lat"], c["lon"], c["woj"], "gmina"
    return None


@lru_cache(maxsize=1)
def _woj_polys():
    g = json.load(open(os.path.join(BASE, "wojewodztwa.json"), encoding="utf-8"))
    out = []
    for f in g["features"]:
        geo = f["geometry"]
        polys = [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]
        out.append((f["properties"]["name"], polys))
    return out


def _in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def woj_of(lat, lon):
    if lat is None or lon is None:
        return None
    for name, polys in _woj_polys():
        for p in polys:
            if _in_ring(lon, lat, p[0]) and not any(_in_ring(lon, lat, h) for h in p[1:]):
                return name
    return None
