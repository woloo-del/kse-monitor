"""Archiwum dzienne: po północy zapisuje kompletne dane poprzedniej doby do katalogu dane/RRRR/MM/DD
na gałęzi `archive` (z historią). Uruchamiane w workflow po głównym przebiegu; nic nie robi, gdy doba już jest w archiwum.
Użycie: python -m scraper.archive <katalog_out> <katalog_archiwum>"""
import json, os, shutil, sys, datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Warsaw")


def main(out_dir, arch_dir):
    now = dt.datetime.now(TZ)
    d1 = (now.date() - dt.timedelta(days=1)).isoformat()
    y, m, d = d1.split("-")
    target = os.path.join(arch_dir, "dane", y, m, d)
    if os.path.exists(os.path.join(target, "manifest.json")):
        print("archiwum", d1, "już istnieje")
        return 0
    os.makedirs(target, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(out_dir)):
        if name.endswith(f"_{d1}.json"):  # rce_, wlk_, gen_, load_ poprzedniej doby
            shutil.copy2(os.path.join(out_dir, name), os.path.join(target, name))
            copied.append(name)
    for name, new in (("events.json", f"zdarzenia_{d1}_stan_{now:%H%M}.json"), ("nbp.json", "nbp.json"), ("status.json", "status.json")):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(target, new))
            copied.append(new)
    with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"doba": d1, "zapisano": now.isoformat(timespec="seconds"), "pliki": copied}, f, ensure_ascii=False, indent=1)
    # indeks dni
    idx_p = os.path.join(arch_dir, "dane", "index.json")
    try:
        idx = json.load(open(idx_p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        idx = {"dni": []}
    idx["dni"] = sorted(set(idx["dni"]) | {d1})
    json.dump(idx, open(idx_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("zarchiwizowano", d1, copied)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
