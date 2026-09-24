"""Wspólne narzędzia: HTTP, czas, zapis."""
import json, os, time, logging, datetime as dt
from zoneinfo import ZoneInfo
import requests

TZ = ZoneInfo("Europe/Warsaw")
UTC = dt.timezone.utc
OUT = os.environ.get("OUT_DIR", "out")
PREV = os.environ.get("PREV_DIR", "prev/out")
UA = "kse-monitor/1.0 (+https://github.com; dashboard informacyjny)"
log = logging.getLogger("kse")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"})


def get(url, params=None, headers=None, timeout=30, tries=3, expect_json=False):
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, headers=headers or {}, timeout=timeout)
            if r.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json() if expect_json else r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def now_local():
    return dt.datetime.now(TZ)


def iso(d):
    return d.isoformat(timespec="seconds") if d else None


def today_dates():
    d = now_local().date()
    one = dt.timedelta(days=1)
    return {"D-1": (d - one).isoformat(), "D": d.isoformat(), "D+1": (d + one).isoformat()}


def write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def read_prev(name):
    p = os.path.join(PREV, name)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def carry(name):
    """Przepisz poprzedni plik bez zmian (gdy źródło chwilowo nie działa)."""
    old = read_prev(name)
    if old is not None:
        write(name, old)
    return old
