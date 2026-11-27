"""Unit tests for the mailshot helpers."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import health  # noqa: E402
import prepare  # noqa: E402
import render  # noqa: E402
import send  # noqa: E402
import watch  # noqa: E402


def test_parse_rows_dedupes_and_rejects_invalid(tmp_path):
    source = tmp_path / "src.txt"
    source.write_text(
        "poradi;vzdalenost_km;vybrany_email;nazev_uradu;obec;kraj;kod_uradu;lat;lon\n"
        "1;2.50;Matrika@Example.com;Úřad A;A;K;1;49.1;17.2\n"
        "2;9.00;matrika@example.com;Úřad A2;A;K;2;49.1;17.2\n"
        "3;5.00;matrika@example.org;Úřad B;B;K;3;49.1;17.2\n"
        "4;1.00;neni-email;Úřad C;C;K;4;49.1;17.2\n"
        "5;1.00;;Úřad D;D;K;5;49.1;17.2\n",
        encoding="utf-8",
    )

    rows, invalid, duplicates, source_rows = prepare.parse_rows(source)

    assert source_rows == 4
    assert len(rows) == 2
    assert duplicates == ["matrika@example.com"]
    assert len(invalid) == 1
    assert [row["email"] for row in rows] == ["matrika@example.com", "matrika@example.org"]
    assert abs(rows[0]["distance_km"] - 2.5) < 1e-9


def test_build_mime_has_plain_and_html_parts():
    cfg = {
        "from_email": "odesilatel@example.com",
        "from_name": "Test Odesílatel",
        "reply_to": "odesilatel@example.com",
    }
    message, raw = common.build_mime(
        cfg, "matrika@example.cz", "NALÉHAVÉ: test", "<p>ahoj</p>", "ahoj"
    )

    assert raw
    assert message.is_multipart()
    subtypes = {part.get_content_subtype() for part in message.iter_parts()}
    assert subtypes == {"plain", "html"}


def test_classify_reply_bounce_and_own_mail():
    recipients = {"matrika@example.com"}
    our_ids = {"<abc@example.com>"}
    our_threads = {"t-1"}
    our_email = "odesilatel@example.com"
    mark = "Prosba o krátký obřad"

    direct = watch.classify(
        {"from": "Matrika A <matrika@example.com>", "subject": "Re: cokoliv"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert direct and direct["kind"] == "reply" and direct["recipient_email"] == "matrika@example.com"

    by_reference = watch.classify(
        {"from": "info@example.org", "subject": "Re: dotaz", "references": "<abc@example.com>"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert by_reference and by_reference["kind"] == "reply"

    by_subject = watch.classify(
        {"from": "info@example.org", "subject": "RE: Prosba o krátký obřad"},
        recipients,
        our_ids,
        our_threads,
        our_email,
        subject_mark=mark,
    )
    assert by_subject and by_subject["kind"] == "reply"

    no_mark = watch.classify(
        {"from": "info@example.org", "subject": "RE: Prosba o krátký obřad"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert no_mark is None  # subject matching is opt-in via config

    bounce = watch.classify(
        {"from": "Mail Delivery System <mailer-daemon@example.org>", "x-failed-recipients": "matrika@example.com"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert bounce and bounce["kind"] == "bounce" and bounce["recipient_email"] == "matrika@example.com"

    own = watch.classify(
        {"from": "Test <odesilatel@example.com>", "subject": "Re: Prosba o krátký obřad"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert own is None

    unrelated = watch.classify(
        {"from": "kamarad@example.cz", "subject": "Ahoj, jak se máš?"},
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert unrelated is None

    auto = watch.classify(
        {
            "from": "Matrika A <matrika@example.com>",
            "subject": "Automatická odpověď: NALÉHAVÉ",
            "auto-submitted": "auto-replied",
        },
        recipients,
        our_ids,
        our_threads,
        our_email,
    )
    assert auto and auto["kind"] == "autoreply"


def test_dashboard_payload_aggregates(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(common.SCHEMA)
    conn.execute(
        "INSERT INTO recipients(email, office, obec, kraj, distance_km, status, sent_at, replied_at)"
        " VALUES('a@example.com','Úřad A','A','K',1.0,'sent','2026-11-23 10:00:00','2026-11-23 11:00:00')"
    )
    conn.execute(
        "INSERT INTO recipients(email, office, obec, kraj, distance_km, status)"
        " VALUES('b@example.org','Úřad B','B','K',2.0,'queued')"
    )
    conn.execute(
        "INSERT INTO recipients(email, office, obec, kraj, distance_km, status)"
        " VALUES('c@example.net','Úřad C','C','K',3.0,'bounced')"
    )
    conn.execute(
        "INSERT INTO mail(recipient_id, message_id, thread_id, subject, sent_at, status)"
        " VALUES(1,'<m@example.com>','t-1','NALÉHAVÉ','2026-11-23 10:00:00','sent')"
    )
    conn.execute(
        "INSERT INTO replies(gmail_id, recipient_email, from_addr, subject, snippet, received_at, kind)"
        " VALUES('g1','a@example.com','a@example.com','Re: NALÉHAVÉ','máme termín','2026-11-23 11:00:00','reply')"
    )

    payload = render.build_payload(
        conn,
        {
            "daily_cap": 450,
            "health": {"online_stale_minutes": 35},
            "db_path": str(tmp_path / "mailshot.db"),
        },
    )

    assert payload["total"] == 3
    assert payload["sent"] == 1
    assert payload["queued"] == 1
    assert payload["bounced"] == 1
    assert payload["replied"] == 1
    assert payload["rows"][0][5] == "a@example.com"
    assert payload["recent"][0]["office"] == "Úřad A"
    assert payload["online_stale_minutes"] == 35
    assert payload["done"] is False
    assert payload["last_watch_at"] is None
    assert payload["last_send_at"] is None
    assert payload["done_at"] is None
    assert "online" not in payload  # computed in JS at page load, never baked in
    assert render.local_time("2026-11-23 10:00:00") == "23.11.2026 11:00"
    assert render.local_time("2026-07-23 10:00:00") == "23.07.2026 12:00"
    assert render.local_time("2026-11-23 10:00:00", "UTC") == "23.11.2026 10:00"
    conn.close()


def temp_config(tmp_path):
    """Build a test config from the committed example, never from a local config.json."""
    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    cfg = common.load_config_dict(example)
    cfg.update(
        db_path=str(tmp_path / "mailshot.db"),
        dashboard_path=str(tmp_path / "index.html"),
        log_path=str(tmp_path / "mailshot.log"),
        credentials_path=str(tmp_path / "credentials.json"),
        watch_until="9999-12-31",
        # Tests exercise the normal running campaign; the paused switch has its own test.
        paused={"active": False, "since": "", "reason": ""},
        send_window={"enabled": False},
        notify={"type": "stdout"},
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg, str(path)


def pause_config(cfg, path, active=True, reason="test pauza"):
    cfg["paused"] = {"active": active, "since": "24. 11. 2026 10:32", "reason": reason}
    Path(path).write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def test_set_meta_survives_reopen(tmp_path):
    cfg, _ = temp_config(tmp_path)
    conn = common.connect(cfg)
    assert common.get_meta(conn, "missing", "fallback") == "fallback"
    common.set_meta(conn, "last_watch_at", "old")
    common.set_meta(conn, "last_watch_at", common.utcnow())
    expected = common.get_meta(conn, "last_watch_at")
    conn.close()
    conn = common.connect(cfg)
    assert common.get_meta(conn, "last_watch_at") == expected
    conn.close()


def test_health_build_status():
    conn = sqlite3.connect(":memory:")
    conn.executescript(common.SCHEMA)
    cfg = {"daily_cap": 450}
    status = health.build_status(conn, cfg)
    assert status["watch_ok"] is False
    assert status["done"] is False
    assert any("watcher" in p for p in status["problems"])
    assert status["total"] == status["queued"] == status["sent"] == 0
    common.set_meta(conn, "last_watch_at", common.utcnow())
    common.set_meta(conn, "last_send_ok_at", common.utcnow())
    status = health.build_status(conn, cfg)
    assert status["problems"] == []
    assert status["watch_ok"] is True
    assert status["send_ok"] is True
    assert status["done"] is False
    assert not conn.in_transaction  # build_status does not write
    conn.close()


def test_health_stale_cap_and_terminal_status():
    conn = sqlite3.connect(":memory:")
    conn.executescript(common.SCHEMA)
    conn.execute("INSERT INTO recipients(email) VALUES('q@example.cz')")
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(common.TS_FMT)
    common.set_meta(conn, "last_watch_at", stale)
    common.set_meta(conn, "last_send_ok_at", stale)
    cfg = {"daily_cap": 1}
    status = health.build_status(conn, cfg)
    assert not status["watch_ok"] and not status["send_ok"]
    assert len(status["problems"]) == 2
    conn.execute(
        "INSERT INTO mail(recipient_id,sent_at) VALUES(1,?)", (common.utcnow(),)
    )
    assert health.build_status(conn, cfg)["send_ok"] is True
    conn.execute("DELETE FROM mail")
    # Default send threshold is 30 minutes, overridable in config.
    common.set_meta(conn, "last_send_ok_at", (
        datetime.now(timezone.utc) - timedelta(minutes=40)
    ).strftime(common.TS_FMT))
    assert health.build_status(conn, cfg)["send_ok"] is False
    cfg["health"] = {"send_stale_minutes": 90}
    assert health.build_status(conn, cfg)["send_ok"] is True
    cfg["watch_until"] = "2000-01-01"
    assert health.build_status(conn, cfg)["watch_ok"] is True
    del cfg["watch_until"]
    common.set_meta(conn, "watch_ended_at", common.utcnow())
    assert health.build_status(conn, cfg)["watch_ok"] is True
    common.set_meta(conn, "done_at", common.utcnow())
    status = health.build_status(conn, cfg)
    assert status["done"] is True and status["problems"] == []
    common.set_meta(conn, "last_watch_at", "bad timestamp")
    assert health.parse_ts("bad timestamp") is None
    assert health.parse_ts(common.utcnow()).tzinfo == timezone.utc
    conn.close()


def test_health_no_notify_on_second_run(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.executemany(
        "INSERT INTO recipients(email,status) VALUES(?,?)",
        [("sent@example.cz", "sent"), ("bounce@example.cz", "bounced"),
         ("fail@example.cz", "failed")],
    )
    conn.commit()
    conn.close()
    calls = []

    def notify(_cfg, text):
        calls.append(text)
        return True, "test"

    with patch.object(common, "send_notification", side_effect=AssertionError("real notify")):
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert health.main(["--config", path], notify=notify) == 0
            assert output.getvalue().count("MAILSHOT_HEALTH done") == 1
            assert len(output.getvalue().splitlines()) == 1
    assert len(calls) == 1  # completion wins even when watcher is missing
    assert "fronta prázdná" in calls[0]
    assert "odesláno 1, nedoručeno 1, chyb 1 z 3" in calls[0]
    conn = common.connect(cfg)
    assert common.get_meta(conn, "done_at")
    assert health.build_status(conn, cfg)["problems"] == []
    conn.close()


def test_health_alert_cooldown_and_no_notify(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email) VALUES('q@example.cz')")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    common.set_meta(conn, "last_watch_at", old.strftime(common.TS_FMT))
    common.set_meta(conn, "last_send_ok_at", old.strftime(common.TS_FMT))
    calls = []

    def notify(_cfg, text):
        calls.append(text)
        return True, "test"

    with patch.object(common, "send_notification", side_effect=AssertionError("real notify")):
        with contextlib.redirect_stdout(io.StringIO()):
            assert health.main(["--config", path], notify=notify) == 0
            # Ages change but the same problem set must not bypass cooldown.
            common.set_meta(conn, "last_watch_at", (old - timedelta(hours=1)).strftime(common.TS_FMT))
            assert health.main(["--config", path], notify=notify) == 0
            assert len(calls) == 1
            common.set_meta(conn, "last_alert_at", (
                datetime.now(timezone.utc) - timedelta(hours=7)
            ).strftime(common.TS_FMT))
            assert health.main(["--config", path], notify=notify) == 0
            assert len(calls) == 2
            common.set_meta(conn, "last_watch_at", common.utcnow())
            assert health.main(["--config", path], notify=notify) == 0
            assert len(calls) == 3  # changed set bypasses cooldown
            common.set_meta(conn, "last_send_ok_at", common.utcnow())
            assert health.main(["--config", path], notify=notify) == 0
            assert len(calls) == 3
            common.set_meta(conn, "last_send_ok_at", "invalid")
            assert health.main(["--config", path, "--no-notify"], notify=notify) == 0
            assert len(calls) == 3
    conn.close()


def test_health_done_retries_after_failed_notify(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,status) VALUES('s@example.cz','sent')")
    conn.commit()
    conn.close()
    results = [(False, "bridge down"), (True, "ok")]
    calls = []

    def notify(_cfg, text):
        calls.append(text)
        return results.pop(0)

    with contextlib.redirect_stdout(io.StringIO()):
        assert health.main(["--config", path], notify=notify) == 0
    conn = common.connect(cfg)
    assert common.get_meta(conn, "done_at") is None  # not latched on a failed send
    assert common.get_meta(conn, "done_pending") is None
    conn.close()

    with contextlib.redirect_stdout(io.StringIO()):
        assert health.main(["--config", path], notify=notify) == 0
    conn = common.connect(cfg)
    assert common.get_meta(conn, "done_at")
    conn.close()
    assert len(calls) == 2


def test_health_no_notify_writes_nothing(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,status) VALUES('s@example.cz','sent')")
    conn.commit()
    conn.close()

    with contextlib.redirect_stdout(io.StringIO()):
        assert health.main(["--config", path, "--no-notify"]) == 0

    conn = common.connect(cfg)
    assert common.get_meta(conn, "done_at") is None
    assert common.get_meta(conn, "last_alert_at") is None
    assert common.get_meta(conn, "last_alert_key") is None
    conn.close()


def test_health_error_exit_zero(tmp_path):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert health.main(["--config", str(tmp_path / "missing.json")]) == 0
    assert output.getvalue().startswith("MAILSHOT_HEALTH error=")
    assert len(output.getvalue().splitlines()) == 1


def test_sender_heartbeat_early_paths(tmp_path):
    cfg, path = temp_config(tmp_path)
    with patch.object(common, "gmail_service", side_effect=AssertionError("real Gmail")):
        with contextlib.redirect_stdout(io.StringIO()):
            assert send.main(["--config", path, "--dry-run"]) == 0
            conn = common.connect(cfg)
            assert common.get_meta(conn, "last_send_at") is None
            assert send.main(["--config", path]) == 0  # empty queue, no Gmail
            assert common.get_meta(conn, "last_send_at")
            assert common.get_meta(conn, "last_send_summary") == "sent=0 remaining=0 sent_24h=0"
            conn.execute("DELETE FROM meta")
            conn.execute("INSERT INTO recipients(email,distance_km) VALUES('q@example.cz',1)")
            conn.commit()
            assert send.main(["--config", path, "--dry-run"]) == 0
            assert common.get_meta(conn, "last_send_at") is None
            conn.executemany("INSERT INTO mail(recipient_id,sent_at) VALUES(1,?)",
                             [(common.utcnow(),)] * cfg["daily_cap"])
            conn.commit()
            assert send.main(["--config", path, "--dry-run"]) == 0
            assert common.get_meta(conn, "last_send_at") is None
            assert send.main(["--config", path]) == 0
            assert common.get_meta(conn, "last_send_summary") == (
                f"sent=0 remaining=1 sent_24h={cfg['daily_cap']}"
            )
            conn.close()
    html = Path(cfg["dashboard_path"]).read_text(encoding="utf-8")
    assert '"last_send_at": null' not in html  # heartbeat precedes render


def test_sender_heartbeat_batch_and_error(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,distance_km) VALUES('q@example.cz',1)")
    conn.commit()
    with patch.object(common, "gmail_service"), patch.object(
        common, "ensure_label", return_value="label-test"
    ), patch.object(send, "send_one", side_effect=OSError("test transport failure")):
        with contextlib.redirect_stdout(io.StringIO()):
            assert send.main(["--config", path, "--interval", "0"]) == 0
    assert common.get_meta(conn, "last_send_summary") == "sent=0 remaining=1 sent_24h=0"
    assert common.get_meta(conn, "last_send_ok_at") is None  # failed batch is not healthy
    with patch.object(common, "gmail_service"), patch.object(
        common, "ensure_label", return_value="label-test"
    ), patch.object(send, "send_one", return_value=("<test@example.cz>", {
        "id": "test-gmail-id", "threadId": "test-thread-id",
    })):
        with contextlib.redirect_stdout(io.StringIO()):
            assert send.main(["--config", path, "--interval", "0"]) == 0
    assert common.get_meta(conn, "last_send_summary") == "sent=1 remaining=0 sent_24h=1"
    assert common.get_meta(conn, "last_send_ok_at")
    assert common.sent_last_24h(conn) == 1
    conn.close()
    html = Path(cfg["dashboard_path"]).read_text(encoding="utf-8")
    assert '"last_send_at": null' not in html


def test_watch_heartbeats(tmp_path):
    cfg, path = temp_config(tmp_path)
    with patch.object(common, "gmail_service") as service, patch.object(
        common, "ensure_label", return_value="label-test"
    ), patch.object(common, "send_notification", side_effect=AssertionError("real notify")):
        service.return_value.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        with contextlib.redirect_stdout(io.StringIO()):
            assert watch.main(["--config", path]) == 0
        conn = common.connect(cfg)
        assert common.get_meta(conn, "last_watch_at")
        assert common.get_meta(conn, "watch_ended_at") is None
        conn.close()
    cfg["watch_until"] = "2000-01-01"
    Path(path).write_text(json.dumps(cfg), encoding="utf-8")
    with patch.object(common, "gmail_service", side_effect=AssertionError("real Gmail")):
        with contextlib.redirect_stdout(io.StringIO()):
            assert watch.main(["--config", path]) == 0
    conn = common.connect(cfg)
    assert common.get_meta(conn, "last_watch_at")
    assert common.get_meta(conn, "watch_ended_at")
    conn.close()
    assert '"last_watch_at": null' not in Path(cfg["dashboard_path"]).read_text(encoding="utf-8")


def test_dashboard_health_and_json_escaping(tmp_path):
    cfg, _ = temp_config(tmp_path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,office,status) VALUES(?,?,'sent')",
                 ("s@example.cz", "</script><script>alert(1)</script>"))
    common.set_meta(conn, "last_watch_at", common.utcnow())
    common.set_meta(conn, "last_send_at", common.utcnow())
    common.set_meta(conn, "done_at", common.utcnow())
    payload = render.build_payload(conn, cfg)
    assert payload["done"] is True
    assert payload["last_watch_at"]
    assert payload["done_at_display"] != "—"
    html = render.write_dashboard(cfg, conn).read_text(encoding="utf-8")
    assert "</script><script>alert" not in html
    assert "<\\/script><script>alert" in html
    assert ">Stav<" in html and ">Rozesílá<" in html
    assert "DATA.online =" in html and "Date.now()" in html
    assert "Vše rozesláno" in html and "dokončeno " in html
    conn.close()


def test_sender_paused_guard_blocks_batch(tmp_path):
    cfg, path = temp_config(tmp_path)
    pause_config(cfg, path)
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,distance_km) VALUES('q@example.cz',1)")
    conn.commit()
    out = io.StringIO()
    with patch.object(common, "gmail_service", side_effect=AssertionError("real Gmail")):
        with contextlib.redirect_stdout(out):
            assert send.main(["--config", path]) == 0
            assert send.main(["--config", path, "--limit", "1"]) == 0
            # dry-run must keep working so a paused run can still be inspected
            assert send.main(["--config", path, "--dry-run"]) == 0
    printed = out.getvalue()
    assert printed.count("MAILSHOT_SEND PAUSED") == 2
    assert "test pauza" in printed
    assert "dry_run selected=1" in printed
    assert conn.execute(
        "SELECT COUNT(*) FROM recipients WHERE status='queued'"
    ).fetchone()[0] == 1
    assert common.get_meta(conn, "last_send_at") is None  # guard exits before heartbeat
    conn.close()


def test_dashboard_shows_pause_banner(tmp_path):
    cfg, path = temp_config(tmp_path)
    conn = common.connect(cfg)
    payload = render.build_payload(conn, cfg)
    assert payload["paused"] is False
    assert payload["paused_reason"] == ""

    pause_config(cfg, path, reason="matriky se ozývají")
    payload = render.build_payload(conn, cfg)
    assert payload["paused"] is True
    assert payload["paused_since"] == "24. 11. 2026 10:32"
    assert payload["paused_reason"] == "matriky se ozývají"

    html = render.write_dashboard(cfg, conn).read_text(encoding="utf-8")
    assert '"paused": true' in html
    assert "matriky se ozývají" in html
    assert 'id="banner"' in html and "banner.classList.add('on')" in html
    assert "● pozastaveno" in html
    conn.close()


def test_health_skips_send_staleness_while_paused(tmp_path):
    cfg, _ = temp_config(tmp_path)
    cfg["paused"] = {"active": True, "since": "24. 11. 2026 10:32", "reason": "pauza"}
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,status) VALUES('q@example.cz','queued')")
    conn.commit()
    common.set_meta(conn, "last_watch_at", common.utcnow())  # only the sender is idle here
    # No sender heartbeat and an open queue: normally an alert, while paused it is expected.
    status = health.build_status(conn, cfg)
    assert status["paused"] is True
    assert status["send_ok"] is True
    assert status["problems"] == []
    cfg["paused"]["active"] = False
    status = health.build_status(conn, cfg)
    assert status["paused"] is False
    assert status["send_ok"] is False
    assert any("odesílání" in problem for problem in status["problems"])
    conn.close()


def test_token_path_uses_config_home(tmp_path):
    with patch.dict("os.environ", {"MAILSHOT_CONFIG_HOME": str(tmp_path)}):
        assert common.token_path({}) == tmp_path / "token.json"
        assert common.token_path({"token_path": str(tmp_path / "custom.json")}) == tmp_path / "custom.json"


def test_gmail_service_requires_token(tmp_path):
    cfg = {
        "token_path": str(tmp_path / "missing-token.json"),
        "credentials_path": str(tmp_path / "credentials.json"),
    }
    try:
        common.gmail_service(cfg)
    except SystemExit as exc:
        assert "scripts/auth.py" in str(exc)
    else:  # pragma: no cover - only runs when the guard is missing
        raise AssertionError("gmail_service must refuse a missing token")


def test_notify_none_stdout_and_errors():
    assert common.send_notification({}, "x") == (True, "disabled")
    assert common.send_notification({"notify": {"type": "none"}}, "x") == (True, "disabled")
    assert common.send_notification({"notify": {"type": "stdout"}}, "ahoj") == (True, "stdout")
    ok, detail = common.send_notification({"notify": {"type": "webhook"}}, "x")
    assert ok is False and "webhook_url" in detail
    ok, detail = common.send_notification({"notify": {"type": "carrier-pigeon"}}, "x")
    assert ok is False and "notify.type" in detail


def test_notify_webhook_posts_json():
    captured = {}

    class Response:
        status = 200

        def read(self):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["content_type"] = request.headers.get("Content-type")
        return Response()

    cfg = {"notify": {"type": "webhook", "webhook_url": "https://hooks.example.com/abc"}}
    with patch("urllib.request.urlopen", fake_urlopen):
        ok, detail = common.send_notification(cfg, "nová odpověď")
    assert ok is True and "ok" in detail
    assert captured["url"] == "https://hooks.example.com/abc"
    assert captured["body"] == {"text": "nová odpověď"}
    assert captured["content_type"] == "application/json"


def test_send_window_closed(tmp_path):
    cfg, path = temp_config(tmp_path)
    hour = datetime.now().hour
    cfg["send_window"] = {
        "enabled": True,
        "start_hour": (hour + 1) % 24,
        "end_hour": (hour + 2) % 24,
    }
    Path(path).write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    conn = common.connect(cfg)
    conn.execute("INSERT INTO recipients(email,distance_km) VALUES('q@example.cz',1)")
    conn.commit()
    out = io.StringIO()
    with patch.object(common, "gmail_service", side_effect=AssertionError("real Gmail")):
        with contextlib.redirect_stdout(out):
            assert send.main(["--config", path]) == 0
            assert send.main(["--config", path, "--dry-run"]) == 0
    printed = out.getvalue()
    assert "MAILSHOT_SEND WINDOW_CLOSED" in printed
    assert "dry_run selected=1" in printed
    assert common.get_meta(conn, "last_send_at") is None
    conn.close()


def test_render_templates_uses_config_placeholders(tmp_path):
    cfg, _ = temp_config(tmp_path)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "email.html").write_text(
        "<p>{{ODESILATEL}} {{TELEFON}} {{URAD}} {{DATE}}</p>", encoding="utf-8"
    )
    (templates / "email.txt").write_text("{{TERMIN}} {{OBEC}}", encoding="utf-8")
    cfg["templates_dir"] = str(templates)
    cfg["placeholders"] = {
        "ODESILATEL": "Jana",
        "TELEFON": "+420 xxx xxx xxx",
        "TERMIN": "1.–5. 1. 2027",
    }
    html, text = common.render_templates(cfg, "Úřad X", "Obec Y", "1. 1. 2027")
    assert "Jana" in html and "+420 xxx xxx xxx" in html
    assert "Úřad X" in html and "1. 1. 2027" in html
    assert text == "1.–5. 1. 2027 Obec Y"


def test_privacy_scan_flags_personal_data(tmp_path):
    import check_privacy

    # Built from parts so this test file itself stays clean for the scanner.
    dirty_values = [
        "kontakt: nekdo" + "@gmail.com",
        "tel 771" + " 119 920",
        "cesta C:" + "\\VYVOJ\\projekt",
        "puvodni akce sva" + "tba",
    ]
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("\n".join(dirty_values) + "\n", encoding="utf-8")
    hits = check_privacy.scan([dirty])
    assert len(hits) == 4, hits

    clean = tmp_path / "clean.txt"
    clean.write_text("odesilatel@example.com\nMatrika Ukázka\n", encoding="utf-8")
    assert check_privacy.scan([clean]) == []


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        kwargs = {}
        if function.__code__.co_varnames[: function.__code__.co_argcount] == ("tmp_path",):
            kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="mailshot-test-"))
        try:
            function(**kwargs)
            print(f"PASS {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"TESTS {'ok' if not failures else 'failed'} failures={failures}")
    raise SystemExit(1 if failures else 0)
