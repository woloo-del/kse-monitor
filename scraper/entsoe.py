"""ENTSO-E Transparency: generacja wg paliw (A75) i niedyspozycyjności (A80 jednostki, A78 sieć przesyłowa)."""
import io, os, zipfile, datetime as dt
import xml.etree.ElementTree as ET
from .common import get, write, now_local, iso, TZ, UTC

API = "https://web-api.tp.entsoe.eu/api"
PL = "10YPL-AREA-----S"
PSR = {"B01": "biomasa", "B02": "wegiel_brunatny", "B03": "gaz_z_wegla", "B04": "gaz", "B05": "wegiel_kamienny",
       "B06": "olej", "B09": "geotermia", "B10": "woda_pompowa", "B11": "woda_przeplywowa", "B12": "woda_zbiornikowa",
       "B14": "atom", "B15": "inne_oze", "B16": "pv", "B17": "odpady", "B18": "wiatr_morski", "B19": "wiatr",
       "B20": "inne", "B25": "magazyny"}
PSR_PL = {"B01": "biomasa", "B02": "węgiel brunatny", "B03": "gaz z węgla", "B04": "gaz", "B05": "węgiel kamienny",
          "B06": "olej", "B10": "szczytowo-pompowa", "B11": "woda przepływowa", "B12": "woda zbiornikowa",
          "B15": "inne OZE", "B16": "PV", "B17": "odpady", "B19": "wiatr", "B20": "inne", "B25": "magazyn"}


def _token():
    t = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not t:
        raise RuntimeError("brak sekretu ENTSOE_TOKEN")
    return t


def _ns(tag):
    return tag.split("}", 1)[-1]


def _find(el, name):
    for c in el.iter():
        if _ns(c.tag) == name:
            return c
    return None


def _text(el, name):
    c = _find(el, name)
    return c.text.strip() if c is not None and c.text else None


def _children(el, name):
    return [c for c in el if _ns(c.tag) == name]


def _period_utc(date):
    """Doba lokalna -> (start, end) w UTC, format yyyyMMddHHmm."""
    d = dt.date.fromisoformat(date)
    s = dt.datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(UTC)
    e = (dt.datetime(d.year, d.month, d.day, tzinfo=TZ) + dt.timedelta(days=1)).astimezone(UTC)
    # doba lokalna ma 23/24/25 h – liczymy koniec od lokalnej północy następnego dnia
    e = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0), tzinfo=TZ).astimezone(UTC)
    return s, e


def _res_minutes(res):
    return {"PT15M": 15, "PT30M": 30, "PT60M": 60}.get(res, 15)


def generation(date):
    s, e = _period_utc(date)
    r = get(API, params={"securityToken": _token(), "documentType": "A75", "processType": "A16", "in_Domain": PL,
                         "periodStart": s.strftime("%Y%m%d%H%M"), "periodEnd": e.strftime("%Y%m%d%H%M")}, timeout=60)
    root = ET.fromstring(r.content)
    if _ns(root.tag) == "Acknowledgement_MarketDocument":
        reason = _text(root, "text") or "brak danych"
        return None, reason
    nq = int((e - s).total_seconds() // 900)
    series = {}
    for ts in root.iter():
        if _ns(ts.tag) != "TimeSeries":
            continue
        psr = _text(ts, "psrType")
        consumption = _find(ts, "outBiddingZone_Domain.mRID") is not None
        key = PSR.get(psr, psr) + ("_pobor" if consumption else "")
        arr = series.setdefault(key, [None] * nq)
        for per in _children(ts, "Period"):
            start = dt.datetime.fromisoformat(_text(per, "start").replace("Z", "+00:00"))
            step = _res_minutes(_text(per, "resolution"))
            pts = {}
            for p in _children(per, "Point"):
                pts[int(_text(p, "position"))] = float(_text(p, "quantity"))
            if not pts:
                continue
            # krzywa A03: brakujące pozycje = wartość poprzedniego punktu; ostatni punkt trwa do końca okresu,
            # ale nie przedłużamy go poza ostatnią opublikowaną pozycję (dane bieżące są niepełne)
            last = max(pts)
            val = None
            for pos in range(1, last + 1):
                if pos in pts:
                    val = pts[pos]
                t0 = start + dt.timedelta(minutes=step * (pos - 1))
                for k in range(step // 15):
                    q = int((t0 + dt.timedelta(minutes=15 * k) - s).total_seconds() // 900)
                    if 0 <= q < nq:
                        arr[q] = round(val, 1)
    labels = []
    for q in range(nq):
        t = (s + dt.timedelta(minutes=15 * (q + 1))).astimezone(TZ).strftime("%H:%M")
        labels.append("24:00" if q == nq - 1 and t == "00:00" else t)
    return {"date": date, "t": labels, "series": series, "src": "ENTSO-E A75", "fetched_at": iso(now_local())}, None


def _unzip_docs(content):
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return [z.read(n) for n in z.namelist() if n.lower().endswith(".xml")]
    return [content]


def _parse_dt(date_s, time_s):
    if not date_s:
        return None
    s = date_s + "T" + (time_s or "00:00:00Z").replace("Z", "+00:00")
    if "+" not in s[10:]:
        s += "+00:00"
    return dt.datetime.fromisoformat(s)


def unavailability(doc_type):
    """A80 = jednostki wytwórcze, A78 = infrastruktura przesyłowa. Okno: teraz–+2 doby."""
    now = now_local().astimezone(UTC)
    s = (now - dt.timedelta(days=1)).strftime("%Y%m%d0000")
    e = (now + dt.timedelta(days=2)).strftime("%Y%m%d0000")
    params = {"securityToken": _token(), "documentType": doc_type, "periodStart": s, "periodEnd": e}
    if doc_type == "A78":
        params.update({"In_Domain": PL, "Out_Domain": PL})
    else:
        params["BiddingZone_Domain"] = PL
    r = get(API, params=params, timeout=90)
    out = []
    for raw in _unzip_docs(r.content):
        root = ET.fromstring(raw)
        if _ns(root.tag) == "Acknowledgement_MarketDocument":
            continue
        ds = _find(root, "docStatus")
        status = (_text(ds, "value") if ds is not None else "") or ""
        if "A09" in status:  # odwołane
            continue
        for ts in root.iter():
            if _ns(ts.tag) != "TimeSeries":
                continue
            name = (_text(ts, "production_RegisteredResource.name") or _text(ts, "Asset_RegisteredResource.name")
                    or _text(ts, "name") or "")
            loc = _text(ts, "production_RegisteredResource.location.name") or _text(ts, "location.name")
            psr = _text(ts, "production_RegisteredResource.pSRType.psrType") or _text(ts, "asset_PSRType.psrType")
            nominal = _text(ts, "production_RegisteredResource.pSRType.powerSystemResources.nominalP")
            biz = _text(ts, "businessType")
            start = _parse_dt(_text(ts, "start_DateAndOrTime.date"), _text(ts, "start_DateAndOrTime.time"))
            end = _parse_dt(_text(ts, "end_DateAndOrTime.date"), _text(ts, "end_DateAndOrTime.time"))
            avail = None
            for p in ts.iter():
                if _ns(p.tag) == "quantity" and p.text:
                    avail = float(p.text)
                    break
            reason = _text(root, "text") or _text(ts, "text")
            mw = None
            if nominal and avail is not None:
                mw = round(float(nominal) - avail, 1)
            elif nominal:
                mw = round(float(nominal), 1)
            out.append({"name": name, "loc": loc, "psr": psr, "psr_pl": PSR_PL.get(psr, psr), "nominal": float(nominal) if nominal else None,
                        "available": avail, "mw": mw, "planned": biz == "A53", "start": start, "end": end, "reason": reason,
                        "mrid": _text(root, "mRID"), "kind": "gen" if doc_type == "A80" else "przesyl"})
    # tylko zdarzenia trwające teraz lub zaczynające się w ciągu 36 h
    horizon = now + dt.timedelta(hours=36)
    return [x for x in out if (x["end"] is None or x["end"] >= now) and (x["start"] is None or x["start"] <= horizon)]


def run(dates, status):
    msgs, ok = [], True
    for k in ("D-1", "D"):
        try:
            doc, reason = generation(dates[k])
            if doc:
                write(f"gen_{dates[k]}.json", doc)
            else:
                msgs.append(f"A75 {dates[k]}: {reason}")
        except Exception as e:  # noqa: BLE001
            ok = False
            msgs.append(f"A75 {dates[k]}: {str(e)[:120]}")
    events = []
    for t in ("A80", "A78"):
        try:
            events += unavailability(t)
        except Exception as e:  # noqa: BLE001
            msgs.append(f"{t}: {str(e)[:120]}")
            if t == "A80":
                ok = False
    status["entsoe"] = {"ok": ok, "at": iso(now_local()), "msg": "; ".join(msgs) or "A75 generacja wg paliw, A80/A78 niedyspozycyjności"}
    return events
