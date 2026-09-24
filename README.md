# kse-monitor

Scraper danych do dashboardu „Rynek energii PL – monitor dzienny”. Uruchamiany przez GitHub Actions co 15 minut; wynik (pliki JSON) publikowany na gałęzi `data` w katalogu `out/`, bez historii.

## Źródła
| Moduł | Źródło | Dane |
|---|---|---|
| `pse.py` | api.raporty.pse.pl | RCE (`rce-pln`), wielkości podstawowe KSE (`his-wlk-cal`), zapotrzebowanie i prognoza (`kse-load`), plan koordynacyjny (`pk5l-wp`) |
| `entsoe.py` | web-api.tp.entsoe.eu | generacja wg paliw (A75), niedyspozycyjności jednostek (A80) i sieci przesyłowej (A78) |
| `osd.py` | Energa-Operator (JSON), Enea Operator (HTML), PGE Dystrybucja (API per rejon), Tauron Dystrybucja (API per powiat) | awarie i wyłączenia planowane, okno 36 h |
| `run.py` | NBP API | kurs EUR/PLN |

PGE i Tauron (ok. 165 zapytań) pobierane co drugi przebieg, czyli co 30 minut. Stoen Operator nie publikuje danych poza aplikacją JS.

## Geokodowanie
Energa, PGE i Tauron podają współrzędne obszaru wyłączenia. Enea i jednostki ENTSO-E geokodowane po nazwie miejscowości z rejestru PRNG ([jjbartek/polskie-miejscowosci](https://github.com/jjbartek/polskie-miejscowosci), CC BY), a gdy się nie da – po centroidzie gminy (pole `approx: true`). Województwo z granic [ppatrzyk/polska-geojson](https://github.com/ppatrzyk/polska-geojson).

## Konfiguracja
Sekret repozytorium `ENTSOE_TOKEN` (Settings → Secrets and variables → Actions). Bez niego moduł ENTSO-E zgłasza błąd, reszta działa.

Dane mają charakter informacyjny.
