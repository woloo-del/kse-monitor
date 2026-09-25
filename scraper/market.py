"""Dane do zakładek: stos ofert RDN (TGE), rynek bilansujący (PSE), curtailment OZE, tracker BESS, sieć.
Każda funkcja zapisuje własne pliki; błąd jednej nie zatrzymuje pozostałych."""
import re, json, math, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from .common import get, write, read_prev, now_local, iso, TZ
from .pse import _fetch, _end, _num

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept-Language": "pl,en;q=0.9"}


# ---------------------------------------------------------------- 1. stos ofert RDN (TGE, krzywe zagregowane)
def _simplify(points, clear_p, asc):
    """Upraszcza krzywą schodkową: zostawia narożniki istotne wizualnie i wszystkie punkty w pobliżu ceny rozliczenia."""
    out, last = [], None
    pts = [(round(float(p["volume_mwh"]), 1), round(float(p["price_eur"]), 2)) for p in points if p.get("volume_mwh") is not None]
    for i, (v, p) in enumerate(pts):
        near = clear_p is not None and abs(p - clear_p) <= 40
        if last is None or i == len(pts) - 1 or near or abs(v - last[0]) >= 15 or abs(p - last[1]) >= 8:
            out.append([v, p])
            last = (v, p)
    return out


def tge_curves(date):
    d = dt.date.fromisoformat(date)
    h = get("https://tge.pl/krzywe_zagregowane", params={"dateShow": d.strftime("%d-%m-%Y")}, headers=BROWSER, timeout=60).text
    m = re.search(r"Market Coupling\s*(?:>|&gt;)\s*(\d{2}\.\d{2}\.\d{4})", h)
    shown = dt.datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat() if m else None
    if shown != date:
        return None, f"strona pokazuje {shown}"
    m = re.search(r"\bFIX_II_DAYAHEAD15\b\s*=\s*'(.*?)'\s*\n", h, re.S)
    if not m or not m.group(1).strip():
        return None, "brak krzywych 15 min"
    data = json.loads(m.group(1))
    mtu = []
    for x in data:
        p = x.get("price")
        s = _simplify(x.get("sell_aggregated_curve") or [], p, True)
        b = _simplify(x.get("buy_aggregated_curve") or [], p, False)
        mtu.append({"t": (x.get("interval_text") or "").split("-")[-1].replace("24:00", "24:00"), "p": p, "v": x.get("volume"), "s": s, "b": b})
    return mtu, None


def run_curves(dates, status):
    msgs, ok = [], True
    prev_status = (read_prev("status.json") or {}).get("curves", {})
    for k in ("D", "D+1"):
        date = dates[k]
        old = [read_prev(f"curves_{date}_{half}.json") for half in ("a", "b")]
        # dzień już pobrany – nie pobieramy ponownie (krzywe po aukcji się nie zmieniają)
        if all(old):
            for half, o in zip(("a", "b"), old):
                write(f"curves_{date}_{half}.json", o)
            continue
        if k == "D+1" and now_local().hour < 13:
            continue
        try:
            mtu, why = tge_curves(date)
            if not mtu:
                msgs.append(f"{date}: {why}")
                continue
            n = len(mtu)
            for half, part in (("a", mtu[: n // 2]), ("b", mtu[n // 2:])):
                write(f"curves_{date}_{half}.json", {"date": date, "half": half, "mtu": part, "unit": "EUR/MWh, MW",
                                                     "src": "TGE – krzywe zagregowane, market coupling, 15 MTU", "fetched_at": iso(now_local())})
        except Exception as e:  # noqa: BLE001
            ok = False
            msgs.append(f"{date}: {str(e)[:120]}")
    status["curves"] = {"ok": ok, "at": iso(now_local()), "msg": "; ".join(msgs) or "krzywe TGE 15 min"} if (msgs or not prev_status) else {**prev_status, "at": iso(now_local())}


# ---------------------------------------------------------------- 2. rynek bilansujący
RB_FIELDS = {
    "crb-rozl": ["cen_cost", "ckoeb_cost", "ceb_pp_cost", "ceb_sr_cost", "ceb_sr_afrrd_cost", "ceb_sr_afrrg_cost"],
    "crb-prog": ["cen_fcst", "ckoeb_fcst", "ceb_sr_fcst"],
    "eb-rozl": ["eb_d_pp", "eb_w_pp", "eb_afrrd", "eb_afrrg"],
    "en-rozl": ["en_d", "en_w", "balance"],
    "sk": ["sk_cost", "sk_d_fcst", "sk_d1_fcst"],
    "cmbu-tu": ["fcr_d", "fcr_g", "afrr_d", "afrr_g", "mfrrd_d", "mfrrd_g"],
    "mbu-tu": ["fcr_d", "fcr_g", "afrr_d", "afrr_g", "mfrrd_d", "mfrrd_g"],
}
RMB_FIELDS = {
    "cmbp-tp": ["fcr_d", "fcr_g", "afrr_d", "afrr_g", "mfrrd_d", "mfrrd_g", "rr_d", "rr_g"],
    "mbp-tp": ["fcr_d", "fcr_g", "afrr_d", "afrr_g", "mfrrd_d", "mfrrd_g", "rr_d", "rr_g"],
    "zmb": ["zmb_fcrd", "zmb_fcrg", "zmb_afrrd", "zmb_afrrg", "zmb_frrd", "zmb_frrg", "zmb_rrd", "zmb_rrg"],
}


def _series(rep, date, fields, hourly=False):
    rows = _fetch(rep, date)
    if not rows:
        return None
    key = "plan_dtime" if "plan_dtime" in rows[0] else "dtime"
    out = {"t": [_end(str(r.get(key) or "")) for r in rows]}
    for f in fields:
        out[f] = [_num(r.get(f), 2) for r in rows]
    return out


def rb_day(date):
    doc = {"date": date, "src": "PSE: crb-rozl, crb-prog, eb-rozl, en-rozl, sk, cmbu-tu, mbu-tu, cmbp-tp, mbp-tp, zmb", "fetched_at": iso(now_local())}
    for rep, f in list(RB_FIELDS.items()) + list(RMB_FIELDS.items()):
        try:
            s = _series(rep, date, f)
            if s:
                doc[rep] = s
        except Exception as e:  # noqa: BLE001
            doc.setdefault("errors", {})[rep] = str(e)[:120]
    return doc if any(k in doc for k in list(RB_FIELDS) + list(RMB_FIELDS)) else None


def _stack(rows, pcol, qcol, desc=False):
    pts = sorted([(float(r[pcol]), float(r[qcol])) for r in rows if r.get(pcol) is not None and r.get(qcol)], reverse=desc)
    cum, out = 0.0, []
    for p, q in pts:
        cum += q
        out.append([round(cum, 1), round(p, 2)])
    # upraszczanie: co najwyżej ~150 punktów
    if len(out) > 150:
        step = len(out) / 150
        out = [out[int(i * step)] for i in range(150)] + [out[-1]]
    return out


def rb_offer_stack(date):
    """Oferty na energię bilansującą przyjęte na RBN (poeb-rbn): krzywa w górę (ofcg) i w dół (ofcd) dla każdej godziny (pierwszy kwadrans)."""
    rows = _fetch("poeb-rbn", date)
    if not rows:
        return None
    by = {}
    for r in rows:
        t = _end(str(r.get("dtime") or ""))
        by.setdefault(t, []).append(r)
    hours = {}
    for t, rs in by.items():
        if not (t.endswith(":15") or t == "00:15"):
            continue
        hours[t] = {"up": _stack(rs, "ofcg", "ofp"), "down": _stack(rs, "ofcd", "ofp", desc=True), "n": len(rs)}
    return {"date": date, "hours": hours, "src": "PSE poeb-rbn (pierwszy kwadrans każdej godziny)", "fetched_at": iso(now_local()), "n_rows": len(rows)}


def run_rb(dates, status, heavy):
    msgs, ok = [], True
    for k in ("D-1", "D", "D+1"):
        try:
            doc = rb_day(dates[k])
            if doc:
                write(f"rb_{dates[k]}.json", doc)
        except Exception as e:  # noqa: BLE001
            ok = False
            msgs.append(f"rb {dates[k]}: {str(e)[:100]}")
    # stos ofert RB – ciężki raport, raz na godzinę; w pozostałych przebiegach przenosimy poprzedni odczyt
    name = f"rbstack_{dates['D']}.json"
    if heavy or now_local().minute < 15 or not read_prev(name):
        try:
            st = rb_offer_stack(dates["D"])
            if st:
                write(name, st)
            else:
                msgs.append("poeb-rbn: brak danych")
        except Exception as e:  # noqa: BLE001
            msgs.append(f"poeb-rbn: {str(e)[:100]}")
            old = read_prev(name)
            if old:
                write(name, old)
    else:
        write(name, read_prev(name))
    status["rb"] = {"ok": ok, "at": iso(now_local()), "msg": "; ".join(msgs) or "ceny i ilości RB, moce bilansujące, stos ofert RBN"}


# ---------------------------------------------------------------- 3. curtailment OZE
def redoze_day(date):
    rows = _fetch("poze-redoze", date)
    if not rows:
        return None
    f = ["pv_red_balance", "pv_red_network", "wi_red_balance", "wi_red_network"]
    doc = {"date": date, "t": [_end(str(r.get("dtime") or "")) for r in rows], "src": "PSE poze-redoze", "fetched_at": iso(now_local())}
    for c in f:
        doc[c] = [_num(r.get(c)) for r in rows]
    return doc


def run_redoze(dates, status):
    msgs, ok = [], True
    for k in ("D-1", "D", "D+1"):
        try:
            doc = redoze_day(dates[k])
            if doc:
                write(f"redoze_{dates[k]}.json", doc)
        except Exception as e:  # noqa: BLE001
            ok = False
            msgs.append(f"{dates[k]}: {str(e)[:100]}")
    status["redoze"] = {"ok": ok, "at": iso(now_local()), "msg": "; ".join(msgs) or "nierynkowe redysponowanie OZE"}


# ---------------------------------------------------------------- 4. tracker BESS i statystyki dzienne
ETA = 0.85


def arbitrage(prices, e_h, cycles, eta=ETA, dt_h=0.25):
    """Idealny arbitraż (pełna wiedza o cenach) dla 1 MW: DP po poziomach naładowania co 0,25 MWh.
    Ładowanie 1 poziomu kupuje 0,25/√η MWh, rozładowanie sprzedaje 0,25·√η MWh; limit cykli = energia rozładowana ≤ cycles·E."""
    ps = [p for p in prices if p is not None]
    if len(ps) < len(prices) * 0.9:
        return None
    prices = [p if p is not None else 0.0 for p in prices]
    n = int(round(e_h / dt_h))
    cmax = int(cycles * n)
    se = math.sqrt(eta)
    NEG = -1e18
    # V[soc][dis] = najlepszy wynik od kroku t do końca przy pustym magazynie na końcu
    V = [[(0.0 if s == 0 else NEG) for _ in range(cmax + 1)] for s in range(n + 1)]
    for t in range(len(prices) - 1, -1, -1):
        p = prices[t]
        buy = p * dt_h / se
        sell = p * dt_h * se
        W = [[NEG] * (cmax + 1) for _ in range(n + 1)]
        for s in range(n + 1):
            for c in range(cmax + 1):
                best = V[s][c]
                if s < n and V[s + 1][c] > NEG:
                    best = max(best, V[s + 1][c] - buy)
                if s > 0 and c < cmax and V[s - 1][c + 1] > NEG:
                    best = max(best, V[s - 1][c + 1] + sell)
                W[s][c] = best
        V = W
    return round(V[0][0], 1)


def _capture(gen, price):
    num = sum(g * p for g, p in zip(gen, price) if g is not None and p is not None)
    den = sum(g for g, p in zip(gen, price) if g is not None and p is not None)
    avg = [p for p in price if p is not None]
    if den <= 0 or not avg:
        return None, None
    cp = num / den
    base = sum(avg) / len(avg)
    return round(cp, 2), (round(cp / base, 4) if base else None)


def day_stats(date, rce=None, wlk=None, cmbp=None, red=None, crb=None):
    """Zestaw dziennych wskaźników. Brakujące wejścia pobiera z PSE."""
    if rce is None:
        rows = _fetch("rce-pln", date, "dtime,dtime_utc,rce_pln")
        rce = [_num(r.get("rce_pln"), 2) for r in rows]
    if not rce or len([x for x in rce if x is not None]) < 80:
        return None
    if wlk is None:
        rows = _fetch("his-wlk-cal", date, "dtime,dtime_utc,pv,wi,demand")
        wlk = {"pv": [_num(r.get("pv")) for r in rows], "wi": [_num(r.get("wi")) for r in rows]}
    if cmbp is None:
        try:
            rows = _fetch("cmbp-tp", date)
            cmbp = {f: [_num(r.get(f), 2) for r in rows] for f in ("fcr_d", "fcr_g", "afrr_d", "afrr_g", "mfrrd_d", "mfrrd_g")}
        except Exception:  # noqa: BLE001
            cmbp = None
    if red is None:
        try:
            rows = _fetch("poze-redoze", date)
            red = {f: [_num(r.get(f)) for r in rows] for f in ("pv_red_balance", "pv_red_network", "wi_red_balance", "wi_red_network")}
        except Exception:  # noqa: BLE001
            red = None
    if crb is None:
        try:
            rows = _fetch("crb-rozl", date)
            crb = {"cen_cost": [_num(r.get("cen_cost"), 2) for r in rows]}
        except Exception:  # noqa: BLE001
            crb = None
    v = [x for x in rce if x is not None]
    srt = sorted(v)
    st = {"d": date, "avg": round(sum(v) / len(v), 2), "min": min(v), "max": max(v), "neg": sum(1 for x in v if x < 0),
          "zero_or_neg": sum(1 for x in v if x <= 0),
          "spread": round(srt[-1] - srt[0], 2),
          "sp2": round(sum(srt[-8:]) / 8 - sum(srt[:8]) / 8, 2), "sp4": round(sum(srt[-16:]) / 16 - sum(srt[:16]) / 16, 2)}
    for e in (1, 2, 4):
        st[f"arb{e}h"] = arbitrage(rce, e, 1)
        st[f"arb{e}h_2c"] = arbitrage(rce, e, 2)
    pv, wi = (wlk or {}).get("pv"), (wlk or {}).get("wi")
    if pv and len(pv) == len(rce):
        st["pv_cp"], st["pv_cr"] = _capture(pv, rce)
        st["pv_mwh"] = round(sum(x for x in pv if x) / 4, 0)
        st["pv_neg_mwh"] = round(sum(g for g, p in zip(pv, rce) if g and p is not None and p < 0) / 4, 0)
    if wi and len(wi) == len(rce):
        st["wi_cp"], st["wi_cr"] = _capture(wi, rce)
        st["wi_mwh"] = round(sum(x for x in wi if x) / 4, 0)
    if cmbp:
        for f in ("fcr", "afrr", "mfrrd"):
            g, d = cmbp.get(f + "_g") or [], cmbp.get(f + "_d") or []
            st[f + "_g"] = round(sum(x for x in g if x), 1) if g else None
            st[f + "_d"] = round(sum(x for x in d if x), 1) if d else None
    if red:
        for f in ("pv_red_balance", "pv_red_network", "wi_red_balance", "wi_red_network"):
            xs = red.get(f) or []
            st[f] = round(sum(abs(x) for x in xs if x) / 4, 1) if xs else 0.0
    if crb and crb.get("cen_cost"):
        c = [x for x in crb["cen_cost"] if x is not None]
        if c:
            st["cen_avg"], st["cen_max"], st["cen_min"] = round(sum(c) / len(c), 2), max(c), min(c)
    return st


def run_stats(dates, status, backfill_days=60):
    """Historia dziennych wskaźników (stats_daily.json). Dni zamknięte liczone raz; brakujące dociągane partiami."""
    old = read_prev("stats_daily.json") or {"days": []}
    by = {r["d"]: r for r in old.get("days", [])}
    today = dt.date.fromisoformat(dates["D"])
    want = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, backfill_days + 1)]
    # doba D-1 i D-2 odświeżane (dane rozliczeniowe RB i curtailment dochodzą z opóźnieniem)
    refresh = {dates["D-1"], (today - dt.timedelta(days=2)).isoformat()}
    todo = [d for d in want if d not in by or d in refresh or not by[d].get("cen_avg")][:24]
    msgs = []

    def job(d):
        try:
            return d, day_stats(d)
        except Exception as e:  # noqa: BLE001
            return d, {"err": str(e)[:100]}
    with ThreadPoolExecutor(4) as ex:
        for d, st in ex.map(job, todo):
            if st and "err" not in st:
                by[d] = st
            elif st:
                msgs.append(f"{d}: {st['err']}")
    # doba bieżąca i jutrzejsza (RCE znana z góry) – wskaźniki cenowe i arbitraż
    for k in ("D", "D+1"):
        try:
            st = day_stats(dates[k], wlk={}, cmbp=None if k == "D" else {}, red={}, crb={})
            if st:
                st["partial"] = True
                by[dates[k]] = st
        except Exception as e:  # noqa: BLE001
            msgs.append(f"{dates[k]}: {str(e)[:80]}")
    days = sorted(by.values(), key=lambda r: r["d"])[-400:]
    write("stats_daily.json", {"days": days, "eta": ETA, "fetched_at": iso(now_local()),
                               "note": "arbXh = idealny arbitraż RCE 15 min dla 1 MW / X MWh, 1 cykl na dobę (arbXh_2c – do 2 cykli), η=85%; PLN/MW/doba"})
    status["stats"] = {"ok": True, "at": iso(now_local()), "msg": (f"{len(days)} dni; " + "; ".join(msgs))[:300] if msgs else f"{len(days)} dni historii"}


# ---------------------------------------------------------------- 5. sieć i ograniczenia
def grid_day(date):
    doc = {"date": date, "src": "PSE: ogr-oper, przeplywy-mocy, pk5l-wp", "fetched_at": iso(now_local())}
    try:
        rows = _fetch("ogr-oper", date)
        doc["constraints"] = [{"res": r.get("resource_code"), "code": r.get("resource_name"), "node": r.get("node"),
                               "dir": r.get("direction"), "from": r.get("from_dtime"), "to": r.get("to_dtime"),
                               "why": (r.get("add_cond") or "").strip()[:160], "elem": r.get("limiting_element"),
                               "pmax": r.get("pol_max_power_of_unit"), "pmin": r.get("pol_min_power_of_unit")} for r in rows]
    except Exception as e:  # noqa: BLE001
        doc.setdefault("errors", {})["ogr-oper"] = str(e)[:120]
    try:
        rows = _fetch("przeplywy-mocy", date)
        flows = {}
        for r in rows:
            sec = r.get("section_code") or "?"
            f = flows.setdefault(sec, {})
            f[_end(str(r.get("dtime") or ""))] = _num(r.get("value"))
        ts = sorted({t for f in flows.values() for t in f}, key=lambda x: (x == "24:00", x))
        doc["flows"] = {"t": ts, "sections": {s: [f.get(t) for t in ts] for s, f in flows.items()}}
    except Exception as e:  # noqa: BLE001
        doc.setdefault("errors", {})["przeplywy-mocy"] = str(e)[:120]
    try:
        rows = _fetch("pk5l-wp", date)
        fields = ["fcst_pv_tot_gen", "fcst_wi_tot_gen", "grid_demand_fcst", "req_pow_res", "surplus_cap_avail_tso",
                  "gen_surplus_avail_tso_above", "planned_exchange", "sum_unav_oper_cond", "avail_cap_gen_units_stor_prov"]
        doc["plan"] = {"t": [_end(str(r.get("plan_dtime") or "")) for r in rows], **{f: [_num(r.get(f)) for r in rows] for f in fields}}
    except Exception as e:  # noqa: BLE001
        doc.setdefault("errors", {})["pk5l-wp"] = str(e)[:120]
    return doc


def run_grid(dates, status):
    msgs = []
    for k in ("D", "D+1"):
        try:
            doc = grid_day(dates[k])
            write(f"grid_{dates[k]}.json", doc)
            if doc.get("errors"):
                msgs.append(f"{dates[k]}: " + ", ".join(doc["errors"]))
        except Exception as e:  # noqa: BLE001
            msgs.append(f"{dates[k]}: {str(e)[:100]}")
    status["grid"] = {"ok": not msgs, "at": iso(now_local()), "msg": "; ".join(msgs)[:300] or "ograniczenia, przepływy, plan koordynacyjny"}


def run(dates, status, heavy):
    for name, fn in (("curves", lambda: run_curves(dates, status)), ("rb", lambda: run_rb(dates, status, heavy)),
                     ("redoze", lambda: run_redoze(dates, status)), ("stats", lambda: run_stats(dates, status)),
                     ("grid", lambda: run_grid(dates, status))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            status[name] = {"ok": False, "at": iso(now_local()), "msg": str(e)[:200]}
