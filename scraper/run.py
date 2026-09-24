"""Orkiestracja: każde źródło osobno, błąd jednego nie zatrzymuje pozostałych.
Wynik trafia do katalogu out/ (gałąź `data`), skąd zadanie Claude przepisuje go do dashboardu."""
import os, re, sys, logging, datetime as dt
from .common import get, write, read_prev, now_local, iso, today_dates, carry
from . import pse, entsoe, osd, geo

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PLANTS = {  # fragment nazwy jednostki / kod -> miejscowość (lokalizacja elektrowni, przybliżona)
    "bełchat": "Bełchatów", "belchat": "Bełchatów", "bel ": "Bełchatów", "kozien": "Kozienice", "koz ": "Kozienice",
    "opole": "Opole", "opl ": "Opole", "turów": "Bogatynia", "turow": "Bogatynia", "tur ": "Bogatynia", "połaniec": "Połaniec",
    "polaniec": "Połaniec", "pol ": "Połaniec", "jaworzno": "Jaworzno", "jaw ": "Jaworzno", "rybnik": "Rybnik", "ryb ": "Rybnik",
    "dolna odra": "Nowe Czarnowo", "dod ": "Nowe Czarnowo", "pątnów": "Konin", "patnow": "Konin", "pat ": "Konin", "konin": "Konin",
    "ostrołęk": "Ostrołęka", "ostroleka": "Ostrołęka", "łagisza": "Będzin", "lagisza": "Będzin", "łaziska": "Łaziska Górne",
    "laziska": "Łaziska Górne", "siersza": "Trzebinia", "skawina": "Skawina", "stalowa wola": "Stalowa Wola", "włocławek": "Włocławek",
    "wloclawek": "Włocławek", "płock": "Płock", "plock": "Płock", "żarnowiec": "Gniewino", "zarnowiec": "Gniewino",
    "porąbka": "Porąbka", "porabka": "Porąbka", "solina": "Solina", "żydowo": "Żydowo", "zydowo": "Żydowo", "adamów": "Turek",
    "grudziądz": "Grudziądz", "grudziadz": "Grudziądz", "siekierki": "Warszawa", "żerań": "Warszawa", "zeran": "Warszawa",
    "łęg": "Kraków", "karolin": "Poznań", "wrotków": "Lublin", "blachownia": "Kędzierzyn-Koźle", "pomorzany": "Szczecin",
    "elcho": "Chorzów", "kędzierzyn": "Kędzierzyn-Koźle", "puławy": "Puławy", "pulawy": "Puławy", "police": "Police",
}


def geocode_unit(u):
    txt = " " + " ".join(filter(None, [u.get("name"), u.get("loc")])).lower() + " "
    for k, town in PLANTS.items():
        if k in txt:
            r = geo.locality(town)
            if r:
                return r
    # najdłuższa nazwa miasta zawarta w nazwie jednostki
    by_name, _ = geo._gaz()
    best = None
    for w in re.findall(r"[a-ząćęłńóśźż\-]{4,}", txt):
        recs = [r for r in by_name.get(w, []) if r["city"]]
        if recs and (best is None or len(w) > len(best[0])):
            best = (w, recs[0])
    if best:
        r = best[1]
        return r["lat"], r["lon"], r["woj"], "miejscowosc"
    return None


def entsoe_events(units, now):
    out = []
    for u in units:
        g = geocode_unit(u)
        s, e = u["start"], u["end"]
        ev = {"id": f"entsoe:{u.get('mrid')}:{u['name']}", "operator": "PSE" if u["kind"] == "przesyl" else "ENTSO-E",
              "type": u["kind"], "place": u["name"] or (u.get("loc") or "—"),
              "desc": f"{u['psr_pl'] or ''} · moc nominalna {u['nominal'] or '—'} MW · dostępna {u['available'] if u['available'] is not None else '—'} MW · "
                      f"{'planowa' if u['planned'] else 'nieplanowa'}" + (f" · {u['reason']}" if u.get("reason") else ""),
              "start": iso(s), "end": iso(e), "active": bool((s is None or s <= now) and (e is None or e >= now)),
              "planned": u["planned"], "scale": u["mw"], "scale_unit": "MW", "mw": u["mw"]}
        if g:
            ev.update({"lat": g[0], "lon": g[1], "woj": g[2], "approx": True, "geo": "elektrownia (miejscowość)"})
        out.append(ev)
    return out


def nbp(status):
    try:
        d = get("https://api.nbp.pl/api/exchangerates/rates/a/eur/last/30/", params={"format": "json"}, expect_json=True)
        write("nbp.json", {"hist": [{"d": r["effectiveDate"], "v": r["mid"]} for r in d["rates"]], "src": "NBP tabela A", "fetched_at": iso(now_local())})
        status["nbp"] = {"ok": True, "at": iso(now_local()), "msg": "EUR/PLN tabela A"}
    except Exception as e:  # noqa: BLE001
        carry("nbp.json")
        status["nbp"] = {"ok": False, "at": iso(now_local()), "msg": str(e)[:150]}


def main():
    now = now_local()
    dates = today_dates()
    status = {"generated_at": iso(now), "dates": dates}
    heavy = os.environ.get("HEAVY", "auto")
    heavy = (now.minute // 15) % 2 == 0 if heavy == "auto" else heavy == "1"

    pse.run(dates, status)
    nbp(status)
    try:
        units = entsoe.run(dates, status)
    except Exception as e:  # noqa: BLE001
        units = []
        status["entsoe"] = {"ok": False, "at": iso(now), "msg": str(e)[:150]}
    events = osd.run(status, heavy=heavy)
    if status.get("entsoe", {}).get("ok") or units:
        events += entsoe_events(units, now.astimezone(dt.timezone.utc))
    else:
        events += [x for x in (read_prev("events.json") or {}).get("events", []) if x.get("type") in ("gen", "przesyl")]
    events.sort(key=lambda e: (e.get("scale") or 0), reverse=True)
    write("events.json", {"generated_at": iso(now), "events": events})
    status["counts"] = {"events": len(events), "geocoded": sum(1 for e in events if e.get("lat") is not None)}
    write("status.json", status)
    files = sorted(f for f in os.listdir(os.environ.get("OUT_DIR", "out")) if f.endswith(".json"))
    write("manifest.json", {"generated_at": iso(now), "files": files + ["manifest.json"]})
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
