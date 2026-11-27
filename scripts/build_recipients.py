"""Doplní kontakty ze SOVM do data/matriky-cz.csv a vygeneruje seznam pro sender.

SOVM = Seznam orgánů veřejné moci (Czech POINT / DIA), publikovaný jako otevřená data
indexovým XML souborem, který se aktualizuje denně. Skript z něj bere jen e-maily
úřadu, IČO a ID datové schránky — nic víc.

Použití:
  python scripts/build_recipients.py --index http://…  # stáhne SOVM a doplní kontakty
  python scripts/build_recipients.py --index sovm.xml  # totéž z lokálního souboru
  python scripts/build_recipients.py --emit            # jen vygeneruje data/recipients.txt
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASE_CSV = DATA / "matriky-cz.csv"
RECIPIENTS = DATA / "recipients.txt"

SOVM_INDEX_URL = (
    "https://www.czechpoint.gov.cz/spravadat/ovm/datafile.do?format=xml&service=seznamovm"
)
NS = "{http://www.czechpoint.cz/spravadat/p/ovm/datafile/seznamovm/v1}"
SOURCE = "SOVM/DIA"

COLUMNS = [
    "kod_uradu",
    "nazev_uradu",
    "obec",
    "kraj",
    "lat",
    "lon",
    "email",
    "email_typ",
    "email_alternativy",
    "ico",
    "id_datove_schranky",
    "zdroj",
    "aktualizovano",
]

# Instituce mívají víc schránek; rolovou bereme před jmennou, ať mail dojde podatelně.
# Porovnává se jen část před @ a po rozdělení na slova — jinak by "katerina.moravcova@mesto.example.com"
# vypadala jako rolovka kvůli doméně a "kourim@…" kvůli slabice.
ROLE_PREFIXES = (
    "matrik", "podatel", "podani", "epodatel", "posta", "urad", "obec", "mesto", "mestys",
    "info", "sekret", "starost", "kancelar", "radnic", "evid",
)
ROLE_EXACT = {"ou", "mu", "oku"}


def norm(value: str | None) -> str:
    text = unicodedata.normalize("NFD", (value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = "".join(ch if ch.isalnum() or ch == " " else " " for ch in text)
    return " ".join(text.split())


# SOVM obsahuje všechny OVM, ne jen obecní úřady: v "Praze 1" sídlí i ministerstva a akademie.
# Bereme proto jen subjekty typu obec / statutární město, jinak by se do kontaktu zamíchaly cizí
# e-maily (regression: Praha 1 dostala adresu Akademie věd místo své podatelny).
OFFICE_TYPES = ("obec", "statutarni", "magistrat")

# Slova, která se v názvech úřadů střídají ("Obecní úřad Babice" vs "Obec Babice",
# "Úřad městské části Praha 10" vs "Městská část Praha 10"). Po jejich odebrání zůstane
# jádro názvu, které se dá porovnat.
GENERIC_WORDS = {
    "obec", "obce", "obecni", "obecniho", "urad", "uradu", "mestsky", "mestske",
    "mestskeho", "mesto", "mesta", "mestys", "mestska", "cast", "casti",
    "obvod", "obvodu", "magistrat", "magistratu", "statutarni", "statutarniho",
    "hlavni", "hlavniho", "matrika",
}


def core_name(value: str | None) -> str:
    return " ".join(word for word in norm(value).split() if word not in GENERIC_WORDS)


def is_office_type(subject_type: str | None) -> bool:
    value = norm(subject_type)
    return any(marker in value for marker in OFFICE_TYPES)


def is_role_mailbox(email: str) -> bool:
    """Role mailbox = the local part names a function (matrika@, podatelna@), not a person.

    Only the part before @ counts and it is split into words, so `novak@mesto.example.com` stays a
    person while `obec.babice@…` does not.
    """
    local = (email or "").split("@", 1)[0].lower()
    words = local.replace(".", " ").replace("_", " ").replace("-", " ").split()
    if any(word in ROLE_EXACT for word in words):
        return True
    return any(word.startswith(prefix) for word in words for prefix in ROLE_PREFIXES)


def looks_personal(email: str) -> bool:
    """For the report: an address that is not a role mailbox."""
    return bool(email) and not is_role_mailbox(email)


def fetch_index(source: str, timeout: int = 180) -> bytes:
    """Read the SOVM index from a URL or a local file, transparently gunzipping it."""
    if "://" in source:
        request = urllib.request.Request(source, headers={"User-Agent": "matriky-mailshot/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    else:
        payload = Path(source).read_bytes()
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    return payload


def parse_contacts(payload: bytes) -> dict[tuple[str, str], list[dict]]:
    """(obec, kraj) -> office records from the SOVM index.

    The bare (obec, "") key exists only when every subject in that municipality shares one
    IČO — otherwise a fallback match would silently pick a neighbouring office of the same name.
    """
    by_key: dict[tuple[str, str], dict[str, dict]] = {}
    by_core: dict[tuple[str, str], dict[str, dict]] = {}
    by_obec: dict[str, dict[str, dict]] = {}
    for _, element in ET.iterparse(io.BytesIO(payload), events=("end",)):
        if element.tag != NS + "Subjekt":
            continue
        subject_type = element.findtext(NS + "TypSubjektu")
        name = element.findtext(NS + "Nazev") or ""
        obec = element.findtext(NS + "AdresaUradu/" + NS + "ObecNazev") or ""
        kraj = element.findtext(NS + "AdresaUradu/" + NS + "KrajNazev") or ""
        if not obec or not is_office_type(subject_type):
            element.clear()
            continue
        emails: list[tuple[str, str]] = []
        for group in element.findall(NS + "Email"):
            for item in group.findall(NS + "Polozka"):
                address = (item.findtext(NS + "Email") or "").strip()
                if address:
                    emails.append((address, (item.findtext(NS + "Typ") or "").strip()))
        ico = (element.findtext(NS + "ICO") or "").strip()
        record = {
            "ico": ico,
            "ds": (element.findtext(NS + "IdDS") or "").strip(),
            "emails": emails,
        }
        slot = ico or norm(name)
        by_key.setdefault((norm(obec), norm(kraj)), {}).setdefault(slot, record)
        if core_name(name):
            by_core.setdefault((core_name(name), norm(kraj)), {}).setdefault(slot, record)
        by_obec.setdefault(norm(obec), {}).setdefault(slot, record)
        element.clear()

    index = {key: list(records.values()) for key, records in by_key.items()}
    # Jádro názvu je přesnější než adresa: v Praze 10 sídlí víc městských částí.
    for key, records in by_core.items():
        index.setdefault(("jmeno:" + key[0], key[1]), list(records.values()))
    core_all: dict[str, dict[str, dict]] = {}
    for (core, _kraj), records in by_core.items():
        core_all.setdefault(core, {}).update(records)
    for core, records in core_all.items():
        if len(records) == 1:
            index.setdefault(("jmeno:" + core, ""), list(records.values()))
    for obec, records in by_obec.items():
        if len(records) == 1:  # unambiguous municipality name across regions
            index.setdefault((obec, ""), list(records.values()))
    return index


def choose_emails(candidates: list[dict]) -> tuple[str, str, str, str, str]:
    """Pick the primary mailbox: role mailboxes first, then SOVM typ 1, then whatever exists."""
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in candidates:
        for address, typ in record["emails"]:
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append((address, typ))
    if not ordered:
        ico = next((record["ico"] for record in candidates if record["ico"]), "")
        ds = next((record["ds"] for record in candidates if record["ds"]), "")
        return "", "", "", ico, ds

    def rank(item: tuple[str, str]) -> tuple[int, int]:
        address, typ = item
        role = 0 if is_role_mailbox(address) else 1
        return role, 0 if typ == "1" else 1

    ordered.sort(key=rank)
    primary, typ = ordered[0]
    alternatives = ";".join(address for address, _ in ordered[1:6])
    ico = next((record["ico"] for record in candidates if record["ico"]), "")
    ds = next((record["ds"] for record in candidates if record["ds"]), "")
    return primary, typ, alternatives, ico, ds


def load_base(path: Path = BASE_CSV) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def refresh(rows: list[dict], index: dict, today: str | None = None) -> tuple[list[dict], dict]:
    today = today or date.today().isoformat()
    stats = {"matched": 0, "with_email": 0, "personal": 0, "no_email": [], "unmatched": []}
    for row in rows:
        core_key = ("jmeno:" + core_name(row["nazev_uradu"]), norm(row["kraj"]))
        # Jméno má přednost: když se úřad najde podle jádra názvu, adresa obce se už nepoužije —
        # jinak by Praha 9 dostala e-mail Prahy 14, která v Praze 9 sídlí.
        candidates = index.get(core_key) or index.get((core_key[0], ""))
        if candidates is None:
            obec_candidates = index.get((norm(row["obec"]), norm(row["kraj"]))) or index.get(
                (norm(row["obec"]), "")
            )
            # Adresu obce bereme jen když je jednoznačná; v Praze 9 sídlí víc městských částí.
            icos = {item["ico"] for item in obec_candidates or [] if item["ico"]}
            candidates = obec_candidates if len(icos) == 1 else None
        if not candidates:
            stats["unmatched"].append(f"{row['nazev_uradu']} ({row['obec']})")
            row.update(
                email="", email_typ="", email_alternativy="", ico="", id_datove_schranky="",
                zdroj="", aktualizovano="",
            )
            continue
        stats["matched"] += 1
        email, typ, alternatives, ico, ds = choose_emails(candidates)
        if email:
            stats["with_email"] += 1
            if looks_personal(email):
                stats["personal"] += 1
        else:
            stats["no_email"].append(f"{row['nazev_uradu']} ({row['obec']})")
        row.update(
            email=email,
            email_typ=typ,
            email_alternativy=alternatives,
            ico=ico,
            id_datove_schranky=ds,
            zdroj=SOURCE,
            aktualizovano=today,
        )
    return rows, stats


def write_base(rows: list[dict], path: Path = BASE_CSV) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})


def emit_recipients(rows: list[dict], path: Path = RECIPIENTS) -> int:
    """Write the sender's input: one row per office that has an e-mail, ordered by kod."""
    written = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("poradi;vzdalenost_km;vybrany_email;nazev_uradu;obec;kraj;kod_uradu;lat;lon\n")
        for row in sorted(rows, key=lambda item: item["kod_uradu"]):
            if not row.get("email"):
                continue
            written += 1
            handle.write(
                ";".join(
                    [
                        str(written),
                        "0.00",
                        row["email"],
                        row["nazev_uradu"],
                        row["obec"],
                        row["kraj"],
                        row["kod_uradu"],
                        row.get("lat") or "",
                        row.get("lon") or "",
                    ]
                )
                + "\n"
            )
    return written


def emit_missing(rows: list[dict], stats: dict, path: Path | None = None) -> Path:
    """Write the offices that still need a contact, with the reason, so anyone can fix them."""
    target = path or (DATA / "missing-contacts.csv")
    missing = {name for name in stats["unmatched"]}
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write("kod_uradu;nazev_uradu;obec;kraj;duvod\n")
        for row in sorted(rows, key=lambda item: item["kod_uradu"]):
            if row.get("email"):
                continue
            label = f"{row['nazev_uradu']} ({row['obec']})"
            reason = "nema_sovm_zaznam" if label in missing else "sovm_bez_emailu"
            handle.write(
                ";".join(
                    [row["kod_uradu"], row["nazev_uradu"], row["obec"], row["kraj"], reason]
                )
                + "\n"
            )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the registry-office dataset from SOVM.")
    parser.add_argument("--index", help="SOVM index URL or local XML file")
    parser.add_argument("--base", default=str(BASE_CSV), help="CSV with the office list")
    parser.add_argument("--emit", action="store_true", help="write data/recipients.txt")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)

    base_path = Path(args.base)
    if not base_path.is_file():
        print(f"BUILD missing base file: {base_path}", file=sys.stderr)
        return 2

    rows = load_base(base_path)
    if args.index:
        payload = fetch_index(args.index)
        index = parse_contacts(payload)
        rows, stats = refresh(rows, index)
        print(
            f"BUILD sovm_matched={stats['matched']} with_email={stats['with_email']} "
            f"personal_primary={stats['personal']} no_email={len(stats['no_email'])} "
            f"unmatched={len(stats['unmatched'])}"
        )
        for name in stats["no_email"][:50]:
            print(f"BUILD no_email {name}")
        for name in stats["unmatched"][:50]:
            print(f"BUILD unmatched {name}")
        if not args.dry_run:
            write_base(rows, base_path)
            missing = emit_missing(rows, stats)
            print(f"BUILD missing_report={missing}")

    if args.emit or args.dry_run:
        if args.dry_run:
            print(f"BUILD dry_run with_email={sum(1 for row in rows if row.get('email'))}")
        else:
            written = emit_recipients(rows)
            print(f"BUILD emitted={written} -> {RECIPIENTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
