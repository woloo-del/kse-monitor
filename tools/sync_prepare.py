"""Przygotowanie zapisu do bazy dashboardu (uruchamiane przez zadanie Claude, nie przez GitHub Actions).

Pobiera najnowsze pliki z gałęzi `data` i zapisuje w katalogu roboczym dokumenty gotowe do ArtifactData
oraz plan.json: lista wpisów {op, collection, doc_id, file_path} do jednego wywołania action "batch".
Użycie: python3 sync_prepare.py <katalog_wyjściowy>
"""
import json, os, sys, urllib.request, datetime as dt

RAW = "https://raw.githubusercontent.com/woloo-del/kse-monitor/data/out/"
SKIP = {"manifest.json", "cache_tauron.json", "status.json", "events.json"}
MAX_EVENTS_BYTES = 600_000


def fetch(name):
    with urllib.request.urlopen(RAW + name, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main(out):
    os.makedirs(out, exist_ok=True)
    man = fetch("manifest.json")
    plan = []

    def put(doc_id, obj, op="set"):
        p = os.path.join(out, f"{doc_id}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        plan.append({"op": op, "collection": "mkt", "doc_id": doc_id, "file_path": os.path.abspath(p)})

    for name in man["files"]:
        if name in SKIP or not name.endswith(".json"):
            continue
        put(name[:-5], fetch(name))

    ev = fetch("events.json")
    events = ev.get("events", [])
    # limit rozmiaru dokumentu: najpierw odrzucamy najdalsze wyłączenia planowane
    events.sort(key=lambda e: (not e.get("active"), e.get("type") not in ("awaria", "gen", "przesyl"), e.get("start") or ""))
    while events and len(json.dumps(events, ensure_ascii=False)) > MAX_EVENTS_BYTES:
        events = events[: int(len(events) * 0.9)]
    put("events", {"generated_at": ev.get("generated_at"), "events": events, "truncated": len(events) < len(ev.get("events", []))})

    st = fetch("status.json")
    now = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    patch = {"sources": {k: st[k] for k in ("pse", "nbp", "entsoe", "osd") if k in st},
             "osd_detail": st.get("osd_detail", {}), "scraper_at": st.get("generated_at"), "last_run": now}
    put("status", patch, op="update")

    with open(os.path.join(out, "plan.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": man.get("generated_at"), "writes": plan}, f, ensure_ascii=False, indent=1)
    print(json.dumps({"scraper_generated_at": man.get("generated_at"), "writes": len(plan),
                      "events": len(events), "status": {k: v.get("ok") for k, v in patch["sources"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "kse_sync")
