"""Tests for the SOVM → data/matriky-cz.csv builder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_recipients as build  # noqa: E402

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<SeznamOvmIndex xmlns="http://www.czechpoint.cz/spravadat/p/ovm/datafile/seznamovm/v1">
  <Subjekt>
    <Zkratka>babice_uh</Zkratka>
    <ICO>12345678</ICO>
    <Nazev>Obec Babice</Nazev>
    <TypSubjektu>Obec I. Typu</TypSubjektu>
    <AdresaUradu><ObecNazev>Babice</ObecNazev><KrajNazev>Zlínský</KrajNazev></AdresaUradu>
    <IdDS>abc1234</IdDS>
    <Email>
      <Polozka><Typ>1</Typ><Email>jana.novakova@example.com</Email></Polozka>
      <Polozka><Typ>2</Typ><Email>podatelna@example.org</Email></Polozka>
    </Email>
  </Subjekt>
  <Subjekt>
    <Zkratka>akademie</Zkratka>
    <ICO>99999999</ICO>
    <Nazev>Akademie věd</Nazev>
    <TypSubjektu>Orgán státní správy</TypSubjektu>
    <AdresaUradu><ObecNazev>Babice</ObecNazev><KrajNazev>Zlínský</KrajNazev></AdresaUradu>
    <IdDS>akd0001</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>info@akademie.example.com</Email></Polozka></Email>
  </Subjekt>
  <Subjekt>
    <Zkratka>traplice</Zkratka>
    <ICO>87654321</ICO>
    <Nazev>Obec Traplice</Nazev>
    <TypSubjektu>Obec I. Typu</TypSubjektu>
    <AdresaUradu><ObecNazev>Traplice</ObecNazev><KrajNazev>Zlínský</KrajNazev></AdresaUradu>
    <IdDS>xyz9876</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>starosta@example.net</Email></Polozka></Email>
  </Subjekt>
</SeznamOvmIndex>
"""


def _index() -> dict:
    return build.parse_contacts(FIXTURE.encode("utf-8"))


def test_norm_strips_diacritics_and_punctuation():
    assert build.norm("Týnec nad Sázavou") == "tynec nad sazavou"
    assert build.norm("Praha 1 - Matrika") == "praha 1 matrika"


def test_parse_contacts_reads_emails_ico_and_databox():
    index = _index()
    records = index[("babice", "zlinsky")]
    assert records, "obec must be indexed by (obec, kraj)"
    emails = dict(records[0]["emails"])
    assert emails["podatelna@example.org"] == "2"
    assert records[0]["ico"] == "12345678"
    assert records[0]["ds"] == "abc1234"
    assert ("babice", "") in index  # fallback key without the region


def test_choose_emails_prefers_role_mailbox_over_person():
    primary, typ, alternatives, ico, ds = build.choose_emails(_index()[("babice", "zlinsky")])
    assert primary == "podatelna@example.org"
    assert typ == "2"
    assert "jana.novakova@example.com" in alternatives
    assert ico == "12345678" and ds == "abc1234"


def test_choose_emails_falls_back_to_person_when_it_is_all_we_have():
    primary, typ, alternatives, ico, _ = build.choose_emails(_index()[("traplice", "zlinsky")])
    assert primary == "starosta@example.net"
    assert typ == "1"
    assert alternatives == ""
    assert ico == "87654321"


def test_non_office_subject_in_same_municipality_is_ignored():
    """Regression: a ministry seated in the same obec must never become the office contact."""
    primary, _typ, alternatives, ico, _ds = build.choose_emails(_index()[("babice", "zlinsky")])
    assert primary == "podatelna@example.org"
    assert "akademie.example.com" not in alternatives
    assert ico == "12345678"


def test_core_name_drops_generic_office_words():
    assert build.core_name("Obecní úřad Babice - Matrika") == "babice"
    assert build.core_name("Obec Babice") == "babice"
    assert build.core_name("Úřad městské části Praha 10 - Matrika") == "praha 10"
    assert build.core_name("Městská část Praha 10") == "praha 10"
    assert build.core_name("Magistrát města Liberec - Matrika") == "liberec"


def test_prague_districts_resolve_by_name_not_by_address():
    """SOVM keeps the address municipality, so Praha 10 hosts several districts."""
    fixture = """<?xml version="1.0" encoding="UTF-8"?>
<SeznamOvmIndex xmlns="http://www.czechpoint.cz/spravadat/p/ovm/datafile/seznamovm/v1">
  <Subjekt><Nazev>Městská část Praha 10</Nazev><TypSubjektu>Statutární města</TypSubjektu>
    <ICO>00063941</ICO><AdresaUradu><ObecNazev>Praha 10</ObecNazev>
    <KrajNazev>Hlavní město Praha</KrajNazev></AdresaUradu><IdDS>aaa</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>posta@example.com</Email></Polozka></Email></Subjekt>
  <Subjekt><Nazev>Městská část Praha-Dolní Měcholupy</Nazev><TypSubjektu>Statutární města</TypSubjektu>
    <ICO>00231347</ICO><AdresaUradu><ObecNazev>Praha 10</ObecNazev>
    <KrajNazev>Hlavní město Praha</KrajNazev></AdresaUradu><IdDS>bbb</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>urad@dolnimecholupy.example.com</Email></Polozka></Email></Subjekt>
</SeznamOvmIndex>
"""
    index = build.parse_contacts(fixture.encode("utf-8"))
    rows = [{"kod_uradu": "00205", "nazev_uradu": "Úřad městské části Praha 10 - Matrika",
             "obec": "Praha 10", "kraj": "Hlavní město Praha", "email": "", "email_typ": "",
             "email_alternativy": "", "ico": "", "id_datove_schranky": ""}]
    rows, stats = build.refresh(rows, index, today="2026-11-27")
    assert rows[0]["email"] == "posta@example.com"
    assert rows[0]["ico"] == "00063941"
    assert stats["with_email"] == 1


def test_core_match_without_email_does_not_fall_back_to_another_office():
    """Praha 9 must not inherit the e-mail of Praha 14, which merely sits in Praha 9."""
    fixture = """<?xml version="1.0" encoding="UTF-8"?>
<SeznamOvmIndex xmlns="http://www.czechpoint.cz/spravadat/p/ovm/datafile/seznamovm/v1">
  <Subjekt><Nazev>Městská část Praha 9</Nazev><TypSubjektu>Statutární města</TypSubjektu>
    <ICO>00063851</ICO><AdresaUradu><ObecNazev>Praha 9</ObecNazev>
    <KrajNazev>Hlavní město Praha</KrajNazev></AdresaUradu><IdDS>ppp</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>posta@example.com</Email></Polozka></Email></Subjekt>
  <Subjekt><Nazev>Městská část Praha 14</Nazev><TypSubjektu>Statutární města</TypSubjektu>
    <ICO>00063941</ICO><AdresaUradu><ObecNazev>Praha 9</ObecNazev>
    <KrajNazev>Hlavní město Praha</KrajNazev></AdresaUradu><IdDS>qqq</IdDS>
    <Email><Polozka><Typ>1</Typ><Email>posta@jina.example.org</Email></Polozka></Email></Subjekt>
</SeznamOvmIndex>
"""
    index = build.parse_contacts(fixture.encode("utf-8"))
    rows = [
        {"kod_uradu": "00159", "nazev_uradu": "Úřad městské části Praha 9 - Matrika",
         "obec": "Praha 9", "kraj": "Hlavní město Praha", "email": "", "email_typ": "",
         "email_alternativy": "", "ico": "", "id_datove_schranky": ""},
        {"kod_uradu": "00584", "nazev_uradu": "Úřad městské části Praha 17 - Matrika",
         "obec": "Praha 9", "kraj": "Hlavní město Praha", "email": "", "email_typ": "",
         "email_alternativy": "", "ico": "", "id_datove_schranky": ""},
    ]
    rows, stats = build.refresh(rows, index, today="2026-11-27")
    assert rows[0]["email"] == "posta@example.com"
    assert rows[0]["ico"] == "00063851"
    assert rows[1]["email"] == ""  # Praha 17 nemá v SOVM záznam, zůstane prázdné
    assert stats["with_email"] == 1
    assert len(stats["unmatched"]) == 1
    assert stats["no_email"] == []


def test_looks_personal_flags_names_but_not_role_boxes():
    assert build.looks_personal("jana.novakova@example.com") is True
    assert build.looks_personal("dohnalova@example.com") is True
    assert build.looks_personal("podatelna@example.org") is False
    assert build.looks_personal("obec.babice@example.org") is False


def test_refresh_fills_rows_and_reports_unmatched():
    rows = [
        {"kod_uradu": "10812", "nazev_uradu": "Obecní úřad Babice - Matrika",
         "obec": "Babice", "kraj": "Zlínský", "email": "", "email_typ": "",
         "email_alternativy": "", "ico": "", "id_datove_schranky": ""},
        {"kod_uradu": "99999", "nazev_uradu": "Obecní úřad Nikde - Matrika",
         "obec": "Nikde", "kraj": "Zlínský", "email": "", "email_typ": "",
         "email_alternativy": "", "ico": "", "id_datove_schranky": ""},
    ]
    rows, stats = build.refresh(rows, _index(), today="2026-11-27")
    assert rows[0]["email"] == "podatelna@example.org"
    assert rows[0]["zdroj"] == build.SOURCE
    assert rows[0]["aktualizovano"] == "2026-11-27"
    assert rows[1]["email"] == ""
    assert stats["matched"] == 1 and stats["with_email"] == 1
    assert stats["unmatched"] == ["Obecní úřad Nikde - Matrika (Nikde)"]


def test_emit_recipients_writes_sender_schema(tmp_path):
    rows = [
        {"kod_uradu": "11100", "nazev_uradu": "Obecní úřad B - Matrika", "obec": "B",
         "kraj": "Zlínský", "lat": "49.1", "lon": "17.2", "email": "b@example.com"},
        {"kod_uradu": "10812", "nazev_uradu": "Obecní úřad A - Matrika", "obec": "A",
         "kraj": "Zlínský", "lat": "49.0", "lon": "17.1", "email": "a@example.com"},
        {"kod_uradu": "10000", "nazev_uradu": "Obecní úřad C - Matrika", "obec": "C",
         "kraj": "Zlínský", "lat": "49.2", "lon": "17.3", "email": ""},
    ]
    target = tmp_path / "recipients.txt"
    written = build.emit_recipients(rows, target)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert written == 2
    assert lines[0].startswith("poradi;vzdalenost_km;vybrany_email")
    assert lines[1].split(";")[2] == "a@example.com"  # ordered by kod_uradu
    assert lines[2].split(";")[2] == "b@example.com"
    assert all(line.count(";") == 8 for line in lines)


def test_fetch_index_gunzips_local_file(tmp_path):
    import gzip

    raw = FIXTURE.encode("utf-8")
    target = tmp_path / "index.xml.gz"
    target.write_bytes(gzip.compress(raw))
    assert build.fetch_index(str(target)) == raw
