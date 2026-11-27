# Provoz

Jak nástroj naplánovat, jak poznat, že běží, a co dělat, když se zastaví.

## Tři joby

| Job | Skript | Interval | Proč |
|---|---|---|---|
| sender | `scripts/send.py` | 5 min | odešle jednu dávku 18 zpráv (15 s rozestup) |
| watcher | `scripts/watch.py` | 15 min | odpovědi, bounce, štítek, notifikace, dashboard |
| health | `scripts/health.py` | 1 h | heartbeat kontrola, alerty, oznámení dokončení |

Všechny tři jsou krátké běhy, které skončí — nedrží se v paměti žádný démon.
Stav žije v `data/mailshot.db` (SQLite WAL), takže se joby nepoperou ani když se
překryjí; sender navíc drží `data/send.lock`.

## Linux / macOS (cron)

```cron
*/5  * * * * cd /opt/matriky-mailshot && .venv/bin/python scripts/send.py   >> logs/cron.log 2>&1
*/15 * * * * cd /opt/matriky-mailshot && .venv/bin/python scripts/watch.py  >> logs/cron.log 2>&1
0    * * * * cd /opt/matriky-mailshot && .venv/bin/python scripts/health.py >> logs/cron.log 2>&1
```

## Windows (Task Scheduler)

Jednorázově vytvořte tři úlohy (`schtasks /create`), každou s „Run whether user is
logged on or not" a cestou k `.venv\Scripts\python.exe`:

```powershell
schtasks /create /tn "matriky-send"   /sc minute /mo 5  /tr "C:\matriky\\.venv\Scripts\\python.exe C:\matriky\\scripts\\send.py"
schtasks /create /tn "matriky-watch"  /sc minute /mo 15 /tr "C:\matriky\\.venv\Scripts\\python.exe C:\matriky\\scripts\\watch.py"
schtasks /create /tn "matriky-health" /sc hourly          /tr "C:\matriky\\.venv\Scripts\\python.exe C:\matriky\\scripts\\health.py"
```

Pracovní adresář nastavte na kořen projektu (jinak si skripty nenajdou `config.json`).

## Ověření, že to běží

```bash
python scripts/health.py --no-notify
# MAILSHOT_HEALTH ok queued=15 sent=450 total=465             ← vše v pořádku
# MAILSHOT_HEALTH alert queued=15 sent=450 total=465 problems=["odesílání stojí 90 min a fronta má 15 adres"]
```

Exit kód je vždy 0 — health není test, ale hlídač. Alert se posílá jen při změně
sady problémů a maximálně jednou za `health.alert_cooldown_minutes` (výchozí 6 h).
`--no-notify` nic nezapisuje (žádný cooldown, žádné `done_at`), takže ruční
kontrola nikdy neumlčí skutečný alert.

## Prahy a jejich ladění

| klíč v `config.json` | výchozí | význam |
|---|---|---|
| `health.watch_stale_minutes` | 45 | watcher bez heartbeatu déle = problém |
| `health.send_stale_minutes` | 30 | sender bez úspěšné dávky déle = problém |
| `health.online_stale_minutes` | 35 | kdy dashboard přepne na `offline` |
| `daily_cap` | 450 | klouzavý limit 24 h (držte pod limitem Gmailu) |
| `batch_limit` | 18 | zpráv na jednu dávku |
| `interval_seconds` | 15 | pauza mezi zprávami |

Zvyšovat `batch_limit` nemá smysl — dávka se počítá na 5minutový slot, 18 × 15 s
= 4,5 minuty. Když potřebujete rychleji, zkraťte interval, ne délku dávky.

## Pauza, obnovení, dokončení

```bash
# pauza: nejdřív sender, hned potom health (jinak hlásí falešný alert)
python - <<'PY'
import json, pathlib
p = pathlib.Path("config.json"); cfg = json.loads(p.read_text(encoding="utf-8"))
cfg["paused"] = {"active": True, "since": "2027-01-01 10:00", "reason": "čekáme na odpovědi"}
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
PY
```

Watcher nechte běžet. Obnovení = `"active": false` + znovu zapnout joby; fronta
naváže tam, kde skončila (per-recipient `status`, žádný offset).

Po odeslání celé fronty health jednou pošle `✅ Mailshot: fronta prázdná …`
a zapíše `done_at`; od té chvíle už neupozorňuje. Potom vypněte sender i health,
watcher nechte ještě pár dní běžet podle `watch_until`.

## Aktualizace kontaktů ze SOVM

Seznam úřadů (`data/matriky-cz.csv`) má kontakty ze SOVM ke dni snapshotu. SOVM se
aktualizuje denně, takže před novou kampaní je rozumné kontakty obnovit:

```bash
python scripts/build_recipients.py \
  --index https://www.czechpoint.gov.cz/spravadat/ovm/datafile.do?format=xml&service=seznamovm
python scripts/build_recipients.py --emit      # data/recipients.txt pro sender
```

Skript vypíše `sovm_matched`, `with_email`, `no_email` a `unmatched` a přepíše
`data/missing-contacts.csv` — to je seznam úřadů, které je potřeba doplnit ručně
(např. z jejich webu nebo přes datovou schránku). Párování jde nejdřív podle jádra
názvu úřadu („Městská část Praha 10" = „Úřad městské části Praha 10"), teprve pak
podle obce; obec se použije jen když je v ní jediný úřad, aby Praha 9 nedostala
e-mail Prahy 14.

Bez sítě to jde i z lokálního souboru: `--index sovm-index.xml` (gzip i plain XML).

## Logy a úklid

- `logs/mailshot.log` — JSON řádek na každé odeslání, odpověď a selhání notifikace.
  Rotujte podle potřeby (jeden řádek ≈ 200 B, pro 500 adres je to ≈ 100 kB).
- `data/mailshot.db` — jediný zdroj pravdy; zálohujte před každou změnou šablony.
  SQLite v režimu WAL vytváří i `-wal` a `-shm`, kopírujte všechny tři nebo použijte
  `sqlite3 data/mailshot.db ".backup data/backup.db"`.
- `web/index.html` — generovaný, v gitu je jen `render.py`.

## Co dělat, když se nic neposílá

1. `python scripts/send.py --dry-run` — vypíše, co by se poslalo. Když vypíše
   `selected=0` a fronta není prázdná, je problém ve `status` řádků.
2. `python scripts/health.py --no-notify` — řekne, který heartbeat chybí.
3. `git status` v projektu neřeší nic; stav je v DB:
   `sqlite3 data/mailshot.db "select status, count(*) from recipients group by status;"`.
4. Když je vše zelené a přesto nic neodchází, zkontrolujte hodinu — mimo
   `send_window` (7–19 h) sender záměrně mlčí a napíše `WINDOW_CLOSED`.
