"""PSE – api.raporty.pse.pl: RCE, wielkości podstawowe KSE, zapotrzebowanie i prognoza."""
from .common import get, write, now_local, iso

API = "https://api.raporty.pse.pl/api/"


def _fetch(report, date, select=None):
    params = {"$filter": f"business_date eq '{date}'", "$first": "2000"}
    if select:
        params["$select"] = select
    rows, url = [], API + report
    while url:
        data = get(url, params=params, expect_json=True)
        rows += data.get("value", [])
        url = data.get("nextLink") or data.get("@odata.nextLink")
        params = None
    rows.sort(key=lambda r: str(r.get("dtime_utc") or r.get("dtime") or r.get("udtczas") or ""))
    return rows


def _end(dtime):
    t = (dtime or "")[11:16]
    return "24:00" if t == "00:00" else t


def _num(v, nd=1):
    return None if v is None else round(float(v), nd)


def rce(date):
    rows = _fetch("rce-pln", date, "dtime,dtime_utc,rce_pln")
    if not rows:
        return None
    return {"date": date, "t": [_end(r["dtime"]) for r in rows], "v": [_num(r["rce_pln"], 2) for r in rows],
            "unit": "PLN/MWh", "src": "PSE rce-pln", "fetched_at": iso(now_local())}


WLK = ["demand", "jg", "pv", "wi", "jgm", "jnwrb", "swm_p", "swm_np"]


def wlk(date):
    rows = _fetch("his-wlk-cal", date, "dtime,dtime_utc," + ",".join(WLK))
    if not rows:
        return None
    doc = {"date": date, "t": [_end(r["dtime"]) for r in rows], "src": "PSE his-wlk-cal", "fetched_at": iso(now_local())}
    for c in WLK:
        doc[c] = [_num(r.get(c)) for r in rows]
    return doc


def load(date):
    rows = _fetch("kse-load", date, "dtime,dtime_utc,load_fcst,load_actual")
    if not rows:
        return None
    doc = {"date": date, "t": [_end(r["dtime"]) for r in rows], "fcst": [_num(r.get("load_fcst")) for r in rows],
           "src": "PSE kse-load", "fetched_at": iso(now_local())}
    return doc


def reserve_probe(date):
    """Plan koordynacyjny (pk5l-wp): szukamy pola z rezerwą mocy ponad zapotrzebowanie.
    Zwraca (seria godzinowa lub None, lista nazw pól) – nazwy pól trafiają do statusu do weryfikacji."""
    rows = _fetch("pk5l-wp", date)
    if not rows:
        return None, []
    fields = sorted(rows[0].keys())
    pref = ["gen_surplus_avail_tso_above", "surplus_cap_avail_tso"]
    cand = [f for f in pref if f in fields] or [f for f in fields if "rez" in f.lower()]
    if not cand:
        return None, fields
    f = cand[0]
    tkey = next((k for k in ("plan_dtime", "dtime", "udtczas") if k in rows[0]), None)
    return {"field": f, "t": [_end(str(r.get(tkey) or "")) for r in rows], "v": [_num(r.get(f)) for r in rows]}, fields


def run(dates, status):
    ok, msgs = True, []
    for key, fn, days in (("rce", rce, ["D-1", "D", "D+1"]), ("wlk", wlk, ["D-1", "D"]), ("load", load, ["D", "D+1"])):
        for k in days:
            d = dates[k]
            try:
                doc = fn(d)
                if doc:
                    if key == "load":
                        try:
                            res, fields = reserve_probe(d)
                            if res:
                                doc["reserve"] = res
                            status.setdefault("debug", {})["pk5l_fields"] = fields
                        except Exception as e:  # noqa: BLE001
                            status.setdefault("debug", {})["pk5l_error"] = str(e)[:200]
                    write(f"{key}_{d}.json", doc)
                else:
                    msgs.append(f"{key} {d}: brak publikacji")
            except Exception as e:  # noqa: BLE001
                ok = False
                msgs.append(f"{key} {d}: {str(e)[:120]}")
    status["pse"] = {"ok": ok, "at": iso(now_local()), "msg": "; ".join(msgs) or "rce-pln, his-wlk-cal, kse-load"}
