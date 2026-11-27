"""Batch sender for the registry-office mailshot (Gmail API, label, rolling 24h cap)."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from googleapiclient.errors import HttpError

import common

TEST_PREFIX = "[TEST] "


def record_heartbeat(cfg, conn, sent: int, ok: bool = True) -> None:
    remaining = conn.execute(
        "SELECT COUNT(*) FROM recipients WHERE status='queued'"
    ).fetchone()[0]
    now = common.utcnow()
    common.set_meta(conn, "last_send_at", now)
    if ok:
        # Only a run that actually finished its work refreshes the health heartbeat; a batch
        # dying on 429/timeout must keep last_send_ok_at stale so health can alert.
        common.set_meta(conn, "last_send_ok_at", now)
    common.set_meta(
        conn, "last_send_summary",
        f"sent={sent} remaining={remaining} sent_24h={common.sent_last_24h(conn)}",
    )
    try:
        common.render_dashboard(cfg, conn)
    except Exception as exc:  # dashboard must never break sending
        print(f"MAILSHOT_SEND dashboard_error={exc}")


def today_cs() -> str:
    now = datetime.now()
    return f"{now.day}. {now.month}. {now.year}"


def send_one(service, cfg, label_id, to_email, subject, office, obec, html, text):
    message, raw = common.build_mime(cfg, to_email, subject, html, text)
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    service.users().messages().modify(
        userId="me", id=sent["id"], body={"addLabelIds": [label_id]}
    ).execute()
    return str(message["Message-ID"]), sent


def run_tests(cfg, targets):
    service = common.gmail_service(cfg)
    conn = common.connect(cfg)
    label_id = common.ensure_label(service, conn, cfg["label_name"])
    subject = TEST_PREFIX + cfg["subject"]
    html, text = common.render_templates(cfg, "TESTOVACÍ ÚŘAD", "Test", today_cs())
    for target in targets:
        message_id, sent = send_one(
            service, cfg, label_id, target, subject, "TESTOVACÍ ÚŘAD", "Test", html, text
        )
        common.log_json(cfg, event="test_send", to=target, id=sent["id"])
        print(f"MAILSHOT_TEST sent to={target} id={sent['id']} message_id={message_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the mailshot batch.")
    parser.add_argument("--config", help="path to config.json")
    parser.add_argument("--limit", type=int, help="messages in this batch")
    parser.add_argument("--interval", type=float, help="seconds between messages")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true", help="send [TEST] copy, no DB writes")
    parser.add_argument("--to", help="comma separated test recipients")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.config)
    limit = args.limit or cfg["batch_limit"]
    interval = cfg["interval_seconds"] if args.interval is None else args.interval

    # Hard stop from config.json: while paused, nothing goes to matriky even if a cron
    # job or a human runs the script by mistake. --dry-run and --test (own inbox) still work.
    paused = common.paused_info(cfg)
    if paused["active"] and not args.dry_run and not args.test:
        print(f"MAILSHOT_SEND PAUSED since={paused['since']!r} reason={paused['reason']!r}")
        return 0

    # Business-hours gate: a cron job alone would mail at 3 AM. --dry-run/--test always pass.
    window = cfg.get("send_window") or {}
    if window.get("enabled") and not args.dry_run and not args.test:
        hour = datetime.now().hour
        start = int(window.get("start_hour", 7))
        end = int(window.get("end_hour", 19))
        if not start <= hour < end:
            print(f"MAILSHOT_SEND WINDOW_CLOSED hour={hour} window={start}-{end}")
            return 0

    if args.test:
        targets = [
            item.strip()
            for item in (args.to or ",".join(cfg["test_recipients"])).split(",")
            if item.strip()
        ]
        return run_tests(cfg, targets)

    conn = common.connect(cfg)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM recipients WHERE status='queued'"
    ).fetchone()[0]
    if remaining == 0:
        if not args.dry_run:
            record_heartbeat(cfg, conn, 0)
        print("MAILSHOT_SEND nothing_to_do remaining=0")
        return 0
    sent_24h = common.sent_last_24h(conn)
    if sent_24h >= cfg["daily_cap"]:
        if not args.dry_run:
            record_heartbeat(cfg, conn, 0)
        print(f"MAILSHOT_SEND DAILY_CAP_REACHED sent_24h={sent_24h} cap={cfg['daily_cap']}")
        return 0

    rows = conn.execute(
        "SELECT * FROM recipients WHERE status='queued' "
        "ORDER BY distance_km ASC, id ASC LIMIT ?",
        (limit,),
    ).fetchall()

    if args.dry_run:
        for row in rows:
            print(f"DRY id={row['id']} {row['distance_km']:.2f} km {row['email']} ({row['obec']})")
        print(f"MAILSHOT_SEND dry_run selected={len(rows)} remaining={remaining} sent_24h={sent_24h}")
        return 0

    lock = Path(cfg["db_path"]).parent / "send.lock"
    if not common.acquire_lock(lock):
        print("MAILSHOT_SEND already_running")
        return 0

    sent = failed = 0
    error = ""
    rate_limited = False

    try:
        service = common.gmail_service(cfg)
        label_id = common.ensure_label(service, conn, cfg["label_name"])
        date_cs = today_cs()
        next_at = time.monotonic()
        for row in rows:
            wait = next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_at = time.monotonic() + interval
            html, text = common.render_templates(cfg, row["office"], row["obec"], date_cs)
            try:
                message_id, result = send_one(
                    service,
                    cfg,
                    label_id,
                    row["email"],
                    cfg["subject"],
                    row["office"],
                    row["obec"],
                    html,
                    text,
                )
            except HttpError as exc:
                status = getattr(exc.resp, "status", 0)
                detail = str(exc)[:500]
                if status == 429 or "Mail sending" in detail or "rateLimit" in detail:
                    rate_limited = True
                    error = detail
                    break
                if 400 <= status < 500:
                    conn.execute(
                        "UPDATE recipients SET status='failed', error=? WHERE id=?",
                        (detail, row["id"]),
                    )
                    conn.commit()
                    failed += 1
                    continue
                error = detail
                break
            except OSError as exc:
                error = str(exc)[:500]
                break

            now = common.utcnow()
            conn.execute(
                "UPDATE recipients SET status='sent', sent_at=?, message_id=?, "
                "thread_id=?, error=NULL WHERE id=?",
                (now, message_id, result["threadId"], row["id"]),
            )
            conn.execute(
                "INSERT INTO mail(recipient_id, message_id, thread_id, subject, sent_at, status)"
                " VALUES(?,?,?,?,?, 'sent')",
                (row["id"], message_id, result["threadId"], cfg["subject"], now),
            )
            conn.commit()
            sent += 1
            common.log_json(
                cfg,
                event="send",
                recipient_id=row["id"],
                email=row["email"],
                gmail_id=result["id"],
                thread_id=result["threadId"],
            )
    finally:
        common.release_lock(lock)
        record_heartbeat(cfg, conn, sent, ok=not error and not rate_limited)

    remaining = conn.execute(
        "SELECT COUNT(*) FROM recipients WHERE status='queued'"
    ).fetchone()[0]
    sent_24h = common.sent_last_24h(conn)
    flags = []
    if rate_limited:
        flags.append("rate_limited=1")
    if error:
        flags.append(f"error={error[:120]!r}")
    print(
        f"MAILSHOT_SEND ok sent={sent} failed={failed} remaining={remaining} "
        f"sent_24h={sent_24h}/{cfg['daily_cap']} " + " ".join(flags)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
