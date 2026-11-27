# Data: seznam matričních úřadů

`matriky-cz.csv` je snapshot všech **1 266 matričních úřadů** v ČR s oficiálními kontakty.
`missing-contacts.csv` vypisuje úřady, u kterých se kontakt nepodařilo dohledat, a proč.

## Stav snapshotu — 27. 11. 2026

| | počet |
|---|---|
| matričních úřadů celkem | **1 266** |
| **s e-mailem** | **1 216** (96,1 %) |
| — z toho institucionální schránka (`matrika@`, `podatelna@`, `ou@`, `info@`…) | 1 069 |
| — z toho jmenná schránka úředníka (`prijmeni@`, `jmeno.prijmeni@`) | 147 |
| bez e-mailu, ale se záznamem v SOVM (má IČO + ID datové schránky) | 33 |
| bez záznamu v SOVM (jen identifikace z Přílohy č. 5) | 17 |

Kontakty jsou z SOVM k **27. 11. 2026**. SOVM se aktualizuje denně, takže před novou kampaní
je vhodné je obnovit (viz „Aktualizace kontaktů" níže).

## Kde jsme to vzdali (50 úřadů)

Úplný seznam i s důvodem je v `missing-contacts.csv` (sloupec `duvod`). Rozpad:

### A) SOVM úřad zná, ale nevede u něj e-mail (33)

Nejde o chybu párování — v záznamu SOVM prostě žádná adresa není (typicky magistráty a některé
městské části). Nejrychlejší cesta je **datová schránka**, kterou v datech mají, nebo web úřadu.

- Úřad městské části Praha 17 - Matrika
- Úřad městské části Praha 18 - Matrika
- Městský úřad Týnec nad Sázavou - Matrika
- Městský úřad Kosmonosy - Matrika
- Městský úřad Příbram - Matrika
- Magistrát města České Budějovice - Matrika
- Městský úřad Písek - Matrika
- Městský úřad Vlachovo Březí - Matrika
- Městský úřad Sokolov - Matrika
- Obecní úřad Bukovany - Matrika
- Městský úřad Budyně nad Ohří - Matrika
- Městský úřad Chlumec - Matrika
- Magistrát města Liberec - Matrika
- Úřad městského obvodu Liberec-Vratislavice nad Nisou - Matrika
- Městský úřad Hodkovice nad Mohelkou - Matrika
- Obecní úřad Praskačka - Matrika
- Městský úřad Nové Město nad Metují - Matrika
- Městský úřad Žamberk - Matrika
- Úřad městské části Brno-Královo Pole - Matrika
- Obecní úřad Troubsko - Matrika
- Městský úřad Podivín - Matrika
- Obecní úřad Čechy pod Kosířem - Matrika
- Městský úřad Kojetín - Matrika
- Městský úřad Lipník nad Bečvou - Matrika
- Městský úřad Hanušovice - Matrika
- Městský úřad Uherské Hradiště - Matrika
- Obecní úřad Újezd - Matrika (Zlínský kraj)
- Městský úřad Vizovice - Matrika
- Obecní úřad Dvorce - Matrika
- Obecní úřad Horní Bludovice - Matrika
- Obecní úřad Raškovice - Matrika
- Obecní úřad Petrovice u Karviné - Matrika
- Městský úřad Odry - Matrika

### B) Úřad v indexu SOVM vůbec není (17)

Nemají v SOVM záznam, takže k nim není ani IČO či datová schránka. Kontakt je potřeba vzít
z webu obce. Jsou to většinou malé obce a městské části; identifikace (kód, název, obec, kraj)
v datech zůstává.

- Úřad městyse Nové Dvory - Matrika (Středočeský)
- Úřad městyse Všetaty - Matrika (Středočeský)
- Městský úřad Roztoky - Matrika (okr. Praha-západ) (Středočeský)
- Obecní úřad Petrovice - Matrika (okr. Příbram) (Středočeský)
- Městský úřad Jesenice - Matrika (okr. Rakovník) (Středočeský)
- Úřad městyse Kněževes - Matrika (Středočeský)
- Obecní úřad Petrovice - Matrika (okres. Rakovník) (Středočeský)
- Obecní úřad Roztoky - Matrika (okres. Rakovník) (Středočeský)
- Úřad městyse Stráž - Matrika (Plzeňský)
- Městský úřad Březová - Matrika (Karlovarský kraj) (Karlovarský)
- Obecní úřad Bohuslavice - Matrika (Královehradecký kraj) (Olomoucký)
- Úřad městyse Opatov - Matrika (Vysočina)
- Obecní úřad Bohuslavice - Matrika (Olomoucký kraj) (Olomoucký)
- Obecní úřad Březová - Matrika (Zlínský kraj) (Zlínský)
- Městský úřad Petřvald - Matrika (okr. Karviná) (Moravskoslezský)
- Obecní úřad Petřvald - Matrika (okr. Nový Jičín) (Moravskoslezský)
- Městský úřad Jesenice - Matrika (okres Praha-západ) (Středočeský)

Když některý doplníte, upravte `data/matriky-cz.csv` a spusťte
`python scripts/build_recipients.py --emit`; `missing-contacts.csv` se přepíše sám.

## Odkud data jsou

| Část | Zdroj |
|---|---|
| identifikace úřadů (kód, název, obec, kraj) | **Příloha č. 5 národního standardu pro elektronické vedení matriky** (Ministerstvo vnitra ČR) — seznam matričních úřadů s kódy podle číselníku ČSÚ |
| kontakty (e-mail, IČO, datová schránka) | **SOVM — Seznam orgánů veřejné moci** (Czech POINT / Digitální a informační agentura), otevřená data, aktualizace denně: `https://www.czechpoint.gov.cz/spravadat/ovm/datafile.do?format=xml&service=seznamovm` |
| souřadnice (lat, lon) | poloha adresy úřadu, veřejný údaj |

Snapshot: **27. 11. 2026**. Zdroj prosím uváděj jako „SOVM (Czech POINT / DIA)".

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
