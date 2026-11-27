"""Naplní tabulku příjemců ze seznamu v config.json (source_list)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import common

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def parse_rows(path: str | Path) -> tuple[list[dict], list[tuple[str, str]], list[str], int]:
    """Parse the semicolon list. Returns (unique rows, invalid, duplicates, source row count)."""
    rows: list[dict] = []
    invalid: list[tuple[str, str]] = []
    duplicates: list[str] = []
    index: dict[str, int] = {}
    source_rows = 0

    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for raw in reader:
            if not raw or not (raw.get("vybrany_email") or "").strip():
                continue
            source_rows += 1
            email = (raw.get("vybrany_email") or "").strip().lower()
            office = (raw.get("nazev_uradu") or "").strip()
            if not EMAIL_RE.match(email):
                invalid.append((office, email))
                continue
            try:
                distance = float((raw.get("vzdalenost_km") or "0").replace(",", "."))
            except ValueError:
                distance = 0.0
            try:
                lat = float(raw.get("lat") or 0)
            except ValueError:
                lat = 0.0
            try:
                lon = float(raw.get("lon") or 0)
            except ValueError:
                lon = 0.0
            row = {
                "email": email,
                "office": office,
                "obec": (raw.get("obec") or "").strip(),
                "kraj": (raw.get("kraj") or "").strip(),
                "code": (raw.get("kod_uradu") or "").strip(),
                "distance_km": distance,
                "lat": lat,
                "lon": lon,
            }
            if email in index:
                duplicates.append(email)
                if distance < rows[index[email]]["distance_km"]:
                    rows[index[email]] = row
                continue
            index[email] = len(rows)
            rows.append(row)

    return rows, invalid, duplicates, source_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the recipient table.")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--dry-run", action="store_true", help="parse only, do not write the DB")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.config)
    rows, invalid, duplicates, source_rows = parse_rows(cfg["source_list"])

    if invalid:
        print(f"PREPARE invalid: {invalid[:5]}")
    if args.dry_run:
        print(
            f"PREPARE ok rows={source_rows} unique={len(rows)} "
            f"invalid={len(invalid)} duplicates={len(duplicates)}"
        )
        return 0

    conn = common.connect(cfg)
    inserted = 0
    for row in rows:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO recipients"
            "(email, office, obec, kraj, code, distance_km, lat, lon)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                row["email"],
                row["office"],
                row["obec"],
                row["kraj"],
                row["code"],
                row["distance_km"],
                row["lat"],
                row["lon"],
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0]
    common.log_json(
        cfg,
        event="prepare",
        source_rows=source_rows,
        unique=len(rows),
        inserted=inserted,
        total=total,
        invalid=len(invalid),
        duplicates=len(duplicates),
    )
    print(
        f"PREPARE ok rows={source_rows} unique={len(rows)} invalid={len(invalid)} "
        f"duplicates={len(duplicates)} inserted={inserted} total={total}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
