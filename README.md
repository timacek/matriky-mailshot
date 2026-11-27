# matriky-mailshot

Nástroj pro hromadné oslovení matričních úřadů jedním personalizovaným e-mailem,
s evidencí odpovědí a dashboardem. Vznikl z potřeby obejít stovky matrik, když
člověk potřebuje rychle najít úřad, který mu vyjde vstříc — místo ručního
proklikávání seznamu.

**Kdy se hodí:** hledáte konkrétní termín obřadu, potřebujete oslovit všechny
matriky v zemi najednou a chcete mít přehled, kdo odpověděl. **Kdy ne:** jde o
marketing nebo newsletter — na to použijte ESP (Mailchimp apod.), ne vlastní
schránku.

Nástroj posílá z **vašeho osobního Gmailu** přes Gmail API, drží se limitu
Google (max. 450 zpráv za posledních 24 h) a mezi zprávami čeká 15 s.

## Co to umí

| Skript | Co dělá |
|---|---|
| `scripts/prepare.py` | načte CSV seznam adresátů, zvaliduje a deduplikuje e-maily, naplní SQLite frontu |
| `scripts/build_recipients.py` | postaví seznam úřadů z otevřených dat SOVM a vygeneruje `data/recipients.txt` |
| `scripts/auth.py` | jednorázové OAuth přihlášení, uloží token mimo repo |
| `scripts/send.py` | posílá dávky (výchozí 18 zpráv), lepí Gmail štítek, hlídá 450/24 h a pracovní dobu |
| `scripts/watch.py` | kontroluje inbox i spam, pozná odpověď / automatickou odpověď / bounce, zapíše do DB a oznámí |
| `scripts/render.py` | vygeneruje `web/index.html` — soběstačný dashboard (odesláno / zbývá / odpovědělo) |
| `scripts/health.py` | hodinová kontrola, že sender i watcher opravdu běží; pošle alert, když ne |
| `scripts/check_privacy.py` | před pushem zkontroluje, že v repu nejsou e-maily, telefony ani absolutní cesty |

## Požadavky

- Python 3.11+
- účet Google s dvoufázovým ověřením (Gmail API)
- vlastní Google Cloud projekt s OAuth clientem typu **Desktop app** (zdarma)

## Rychlý start

```bash
# 1. prostředí
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt

# 2. konfigurace
cp config.example.json config.json
# v config.json vyplň from_email, from_name, reply_to a placeholders

# 3. OAuth
python scripts/auth.py          # otevře prohlížeč, uloží token do ~/.config/matriky-mailshot/

# 4. seznam adresátů
python scripts/build_recipients.py --emit      # z přiloženého seznamu 1 266 matrik
# nebo vlastní seznam:
# cp data/example-recipients.txt data/recipients.txt  (a nahraď ukázkové řádky)

# 5. zkouška nanečisto
python scripts/prepare.py --dry-run
```

V Google Cloud Console: **APIs & Services → Library → Gmail API → Enable**, pak
**Credentials → Create credentials → OAuth client ID → Desktop app**, stáhnout JSON
a uložit jako `credentials.json` do kořene projektu. Aplikace je neověřená, takže
Google při prvním přihlášení zobrazí „Google hasn't verified this app" —
pokračujte přes *Advanced → Go to … (unsafe)*. Je to váš vlastní projekt, nikdo
cizí k němu přístup nemá.

## Povinný test před ostrým během

Odeslaný e-mail se nedá vzít zpět. Proto platí: **nic ostrého, dokud si
neprohlédnete testovací kopii ve vlastní schránce.**

```bash
python scripts/prepare.py
python scripts/send.py --dry-run --limit 3        # jen vypíše, komu by se poslalo
python scripts/send.py --test --to vas.email@example.com
```

`--test` pošle zprávu s prefixem `[TEST]` pouze na zadanou adresu a nic nezapíše
do databáze. Zkontrolujte předmět, zalamování, diakritiku a odkaz `tel:`.

## Ostrý provoz

```bash
python scripts/send.py --limit 18     # jedna dávka, 18 zpráv ≈ 4,5 minuty
python scripts/watch.py               # odpovědi a bounce
python scripts/render.py              # dashboard do web/index.html
python scripts/health.py              # kontrola heartbeatů (--no-notify = bez notifikace)
```

Pro trvalý provoz spouštějte `send` každých 5 minut, `watch` každých 15 minut a
`health` každou hodinu (cron, Windows Task Scheduler nebo Hermes cron — viz
`docs/provoz.md`). Sender sám pozná, kdy má fronta dost, a nic neposílá dvakrát:
na to hlídá zámkový soubor `data/send.lock`.

## Limity a slušnost

- **450 zpráv za posledních 24 h** je limit osobního Gmailu (web + API dohromady).
  Počítá se klouzavě, ne po kalendářních dnech; testovací maily se do limitu počítají.
- **15 s mezi zprávami**, 18 zpráv na dávku — ať to nevypadá jako spam a Gmail
  nezačne odpovídat chybou 429.
- **Pracovní doba 7–19 h** (`send_window` v configu). Mimo ni sender nic neodešle.
- **Jeden e-mail na úřad.** Kdo už zprávu dostal, zůstává `sent`; změna předmětu
  se ho netýká a znovu se mu neposílá.
- **Předmět bez urgentních slov a částek.** Velká písmena, „NALÉHAVÉ", „URGENT"
  a sumy v předmětu spolehlivě chytají filtry českých úřadů. Termín patří do těla.
- **Odpovězte každému, kdo se ozve.** Nástroj je na jednorázovou prosbu, ne na
  opakované rozesílky.

## Formát vstupního souboru

UTF-8, středník jako oddělovač, hlavička povinná:

```text
poradi;vzdalenost_km;vybrany_email;nazev_uradu;obec;kraj;kod_uradu;lat;lon
1;0.00;matrika@example.com;Městský úřad Ukázka;Ukázka;Ukázkový kraj;1001;50.0;14.0
```

| sloupec | význam |
|---|---|
| `poradi` | pořadí v seznamu (nepovinné, jen pro orientaci) |
| `vzdalenost_km` | **řadí odesílání** — od nejmenší hodnoty; čím dřív, tím blíž |
| `vybrany_email` | cílová adresa; prázdné a nevalidní řádky se přeskočí, duplicity se sloučí |
| `nazev_uradu`, `obec` | vkládají se do šablony jako `{{URAD}}` a `{{OBEC}}` |
| `kraj`, `kod_uradu`, `lat`, `lon` | jen evidence a dashboard |

Přiložený seznam (`data/matriky-cz.csv`) se do tohoto formátu převádí příkazem
`python scripts/build_recipients.py --emit`. Vzdálenosti v datech nejsou — sloupec
`vzdalenost_km` je `0`, takže pořadí určuje kód úřadu. Chcete-li posílat „od nejbližšího",
doplňte si vlastní hodnoty (např. z kilometrovníku).

## Seznam matrik (1 266 úřadů)

Repo obsahuje **kompletní seznam matričních úřadů ČR s oficiálními kontakty** —
`data/matriky-cz.csv`. Nemusíte nic dolovat z webů měst:

- **Snapshot 27. 11. 2026:** 1 266 úřadů, z toho **1 216 s e-mailem** — 1 069 institucionálních
  schránek (`matrika@`, `podatelna@`, `ou@`…) a 147 jmenných schránek úředníků.
- **50 úřadů bez kontaktu:** 33 má v SOVM záznam bez e-mailu (v datech je proto aspoň IČO
  a ID datové schránky), 17 v SOVM vůbec není. Všechny i s důvodem jsou v
  `data/missing-contacts.csv` a rozepsané v [`data/README.md`](data/README.md#kde-jsme-to-vzdali-50-úřadů).
- identifikace úřadů pochází z Přílohy č. 5 národního standardu pro elektronické vedení
  matriky (MVČR), kontakty z **SOVM** (Czech POINT / DIA) — otevřená data s denní aktualizací.
- zdroj, licenční režim a pokrytí jsou popsané v [`data/README.md`](data/README.md).
- kontakty se v čase mění; obnovit je umí `python scripts/build_recipients.py --index …`
  (viz `docs/provoz.md`).

Tohle není marketingová databáze — jsou to kontaktní údaje úřadů pro jednání s úřadem.
Oslovujte je věcně a ideálně jednou.

## Kde co je

```text
config.json              vaše nastavení (vypnuté z gitu)
credentials.json         OAuth client z Google Cloud (vypnuté z gitu)
data/matriky-cz.csv      seznam 1 266 matričních úřadů s kontakty (SOVM)
data/missing-contacts.csv úřady bez dohledaného kontaktu + důvod
data/recipients.txt      vstupní seznam pro sender (generuje build_recipients.py)
data/mailshot.db         SQLite: recipients, mail, replies, meta
logs/mailshot.log        JSON řádky o každém odeslání a odpovědi
templates/email.html     HTML šablona (600px tabulka, inline CSS)
templates/email.txt      plain-text alternativa
web/index.html           generovaný dashboard
```

Šablona používá `{{URAD}}`, `{{OBEC}}`, `{{DATE}}` (doplní skript) a vlastní
placeholdery z `config.json → placeholders` (`{{ODESILATEL}}`, `{{TELEFON}}`,
`{{TELEFON_TEL}}`, `{{TERMIN}}`). `{{TELEFON_TEL}}` patří do odkazu `tel:`,
`{{TELEFON}}` do viditelného textu.

## Pauza a obnovení

Když chcete přestat posílat, ale nechcete o nic přijít:

1. v `config.json` nastavte `"paused": {"active": true, "since": "…", "reason": "…"}` —
   sender pak odmítne dávku i při ručním spuštění (`MAILSHOT_SEND PAUSED …`),
2. pozastavte cron `send` a `health` (health jinak hlásí „odesílání stojí"),
3. `watch` nechte běžet — odpovědi na už odeslané maily chodí dny.

Dashboard sám vykreslí banner **⏸ Pozastaveno** s důvodem. Obnovení: `active: false`
a znovu zapnout oba joby; fronta naváže tam, kde skončila.

## Řešení potíží

| Příznak | Příčina a řešení |
|---|---|
| `Chybí token … spusť: python scripts/auth.py` | token vypršel nebo se smazal; spusťte `auth.py` znovu |
| `HttpError 429` / `Mail sending` | Gmail rate limit; dávka se ukončí, zbytek zůstane `queued` a dojde na něj příští běh |
| `ZoneInfoNotFoundError: Europe/Prague` | na Windows chybí balíček `tzdata` (`pip install tzdata`) |
| `MAILSHOT_SEND already_running` | zůstal `data/send.lock` po pádu; po 15 min se uvolní sám, jinak ho smažte |
| Gmail štítek se nevytvořil | token nemá scope `gmail.modify`; spusťte `auth.py` znovu |
| Dashboard ukazuje `offline` | watcher neběžel (`last_watch_at` je starý); zkontrolujte cron a `health.py` |

## Soukromí

Repo neobsahuje nic z konkrétní kampaně: žádnou korespondenci, žádné jméno odesílatele,
žádné vzdálenosti. Obsahuje ale seznam matričních úřadů (`data/matriky-cz.csv`) — je to
otevřená datová sada SOVM, u které správce v Národním katalogu dat deklaruje, že neobsahuje
osobní údaje, a přebírá se s uvedením zdroje (viz [`data/README.md`](data/README.md)).

Před každým pushem spusťte:

```bash
python scripts/check_privacy.py
```

Skript projde soubory sledované gitem a selže (exit 1), když najde e-mail mimo doménu
`example.*` (výjimkou je datový soubor, jehož hlavička musí odpovídat očekávanému schématu),
české telefonní číslo, absolutní cestu na disk nebo stopu soukromého nastavení. Konfigurace,
databáze, logy a tokeny jsou v `.gitignore` — do gitu se nedostanou ani omylem.

## Licence

MIT — viz [LICENSE](LICENSE).
