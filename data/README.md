# Data: seznam matričních úřadů

`matriky-cz.csv` je snapshot všech **1 266 matričních úřadů** v ČR s oficiálními kontakty.
`missing-contacts.csv` vypisuje úřady, u kterých se kontakt nepodařilo dohledat, a proč.

## Odkud data jsou

| Část | Zdroj |
|---|---|
| identifikace úřadů (kód, název, obec, kraj) | **Příloha č. 5 národního standardu pro elektronické vedení matriky** (Ministerstvo vnitra ČR) — seznam matričních úřadů s kódy podle číselníku ČSÚ |
| kontakty (e-mail, IČO, datová schránka) | **SOVM — Seznam orgánů veřejné moci** (Czech POINT / Digitální a informační agentura), otevřená data, aktualizace denně: `https://www.czechpoint.gov.cz/spravadat/ovm/datafile.do?format=xml&service=seznamovm` |
| souřadnice (lat, lon) | poloha adresy úřadu, veřejný údaj |

Snapshot: **2026-11-27**. Zdroj prosím uváděj jako „SOVM (Czech POINT / DIA)".

## Licence a osobní údaje

Datová sada SOVM je v Národním katalogu dat vedená takto: *neobsahuje autorské dílo*,
*není chráněna zvláštním právem pořizovatele databáze*, *neobsahuje osobní údaje*;
publikována je podle zákona č. 106/1999 Sb. a směrnice EU 2019/1024 **pro další využití
třetími stranami**. Proto ji tento repozitář přebírá s uvedením zdroje.

Některé kontakty jsou jmenné schránky úředníků (`prijmeni@example.com`) — přesně tak je vede
SOVM. Skript se je snaží nepoužít, pokud úřad vede i rolovou schránku (`matrika@`,
`podatelna@`, `ou@`), ale když jiná není, bere ji.

## Formát

```text
kod_uradu;nazev_uradu;obec;kraj;lat;lon;email;email_typ;email_alternativy;ico;id_datove_schranky;zdroj;aktualizovano
```

| sloupec | význam |
|---|---|
| `kod_uradu` | kód matričního úřadu podle číselníku ČSÚ (5 číslic) |
| `nazev_uradu` | oficiální název úřadu |
| `obec`, `kraj` | adresa úřadu (u městských částí se liší od názvu — Praha 14 sídlí v „Praze 9") |
| `lat`, `lon` | souřadnice adresy úřadu |
| `email` | primární kontakt; prázdný = viz `missing-contacts.csv` |
| `email_typ` | typ dle číselníku SOVM (1 = oficiální, 2 = další) |
| `email_alternativy` | až 5 dalších schránek téhož úřadu, oddělené `;` |
| `ico`, `id_datove_schranky` | IČO a ID datové schránky — funguje i tam, kde e-mail chybí |
| `zdroj`, `aktualizovano` | odkud kontakt je a k jakému dni |

## Pokrytí

- **1 216 z 1 266** úřadů má e-mail (96 %)
- 33 úřadů má v SOVM záznam, ale bez e-mailu (použij datovou schránku nebo web)
- 17 úřadů v SOVM vůbec není (nejčastěji malé obce a městské části)

Aktuální seznam mezer je v `missing-contacts.csv`; po doplnění stačí soubor upravit
a spustit `python scripts/build_recipients.py --emit`.

## Aktualizace kontaktů

```bash
python scripts/build_recipients.py --index https://www.czechpoint.gov.cz/spravadat/ovm/datafile.do?format=xml&service=seznamovm
python scripts/build_recipients.py --emit            # vygeneruje data/recipients.txt pro sender
```

Skript stahuje jediný indexový soubor (2,2 MB gzip / 35 MB XML), páruje úřady podle
jádra názvu („Městská část Praha 10" = „Úřad městské části Praha 10") a až potom podle
obce. Do `data/recipients.txt` jde jen e-mail, název, obec, kraj, kód a souřadnice —
tedy formát, který čeká `scripts/prepare.py`.

## Upozornění

Tohle **není marketingová databáze**. Jsou to kontaktní údaje úřadů pro jednání s úřadem.
Oslovujte je jednotlivě a věcně, ideálně jednou; hromadná korespondence bez věcného důvodu
je důvod, proč takové seznamy přestanou být vítané. Údaje se v čase mění — před ostrým
odesláním ověřte kontakty u úřadů, na kterých vám nejvíc záleží.
