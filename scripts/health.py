"""Hourly heartbeat health check; alerts never fail the cron job."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

import common


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, common.TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def build_status(conn, cfg) -> dict:
    """Read campaign status without writes or notifications."""
    total, queued, sent, failed, bounced = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status='queued'),0),"
        " COALESCE(SUM(status='sent'),0), COALESCE(SUM(status='failed'),0),"
        " COALESCE(SUM(status='bounced'),0) FROM recipients"
    ).fetchone()
    sent_24h = common.sent_last_24h(conn)
    last_send_at = common.get_meta(conn, "last_send_at")
    last_send_ok_at = common.get_meta(conn, "last_send_ok_at")
    last_watch_at = common.get_meta(conn, "last_watch_at")
    done_at = common.get_meta(conn, "done_at")
    paused = common.paused_info(cfg)
    now = datetime.now(timezone.utc)
    health = cfg.get("health", {})
    watch_time, send_time = parse_ts(last_watch_at), parse_ts(last_send_ok_at)
    watch_age = (now - watch_time).total_seconds() / 60 if watch_time else None
    send_age = (now - send_time).total_seconds() / 60 if send_time else None
    watch_ended = bool(common.get_meta(conn, "watch_ended_at")) or (
        bool(cfg.get("watch_until")) and now.strftime("%Y-%m-%d") > cfg["watch_until"]
    )
    watch_ok = watch_ended or (
        watch_age is not None and watch_age <= health.get("watch_stale_minutes", 45)
    )
    send_ok = paused["active"] or queued == 0 or sent_24h >= cfg["daily_cap"] or (
        send_age is not None and send_age <= health.get("send_stale_minutes", 30)
    )
    problems = []
    if not done_at:
        if not watch_ok:
            problems.append(
                "chybí platný heartbeat watcheru" if watch_age is None
                else f"watcher neběžel {int(watch_age)} min"
            )
        if not send_ok:
            problems.append(
                f"chybí platný heartbeat odesílání a fronta má {queued} adres"
                if send_age is None
                else f"odesílání stojí {int(send_age)} min a fronta má {queued} adres"
            )
    return {
        "queued": queued, "sent": sent, "total": total,
        "failed": failed, "bounced": bounced, "sent_24h": sent_24h,
        "last_send_at": last_send_at, "last_watch_at": last_watch_at,
        "last_send_ok_at": last_send_ok_at,
        "watch_ok": watch_ok, "send_ok": send_ok, "paused": paused["active"],
        "done": bool(done_at) or (total > 0 and queued == 0), "problems": problems,
    }


def main(argv=None, notify=None) -> int:
    conn = None
    try:
        parser = argparse.ArgumentParser(description="Check the mailshot health.")
        parser.add_argument("--config", help="path to config.json")
        parser.add_argument("--no-notify", action="store_true", help="disable notifications")
        args = parser.parse_args(argv)
        notify = notify or common.send_notification
        if args.no_notify:
            notify = lambda *_: (True, "disabled")
        cfg = common.load_config(args.config)
        conn = common.connect(cfg)
        status = build_status(conn, cfg)
        now = common.utcnow()
        dry = bool(args.no_notify)
        message = None
        alert_key = None
        if status["done"] and not common.get_meta(conn, "done_at") and not dry:
            # Claim the completion first, notify, then latch done_at only on success so a
            # failed notification retries next hour instead of swallowing the message forever.
            claim = conn.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES('done_pending', ?)", (now,)
            )
            conn.commit()
            if claim.rowcount == 1:
                message = (
                    f"✅ Mailshot: fronta prázdná — odesláno {status['sent']}, "
                    f"nedoručeno {status['bounced']}, chyb {status['failed']} "
                    f"z {status['total']}."
                )
        elif status["problems"] and not dry:
            # Hash the set of problem types, not increasing ages/front-size numbers.
            problem_set = sorted({re.sub(r"\d+", "#", p) for p in status["problems"]})
            alert_key = hashlib.sha256(
                json.dumps(problem_set, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            last_alert = parse_ts(common.get_meta(conn, "last_alert_at"))
            cooldown = cfg.get("health", {}).get("alert_cooldown_minutes", 360)
            if (
                common.get_meta(conn, "last_alert_key") != alert_key
                or last_alert is None
                or (datetime.now(timezone.utc) - last_alert).total_seconds() >= cooldown * 60
            ):
                message = "⚠️ Mailshot: " + "; ".join(status["problems"])
        notification_error = None
        try:
            if message:
                ok, detail = notify(cfg, message)
                if ok and status["done"]:
                    common.set_meta(conn, "done_at", now)
                    conn.execute("DELETE FROM meta WHERE key='done_pending'")
                    conn.commit()
                elif ok:
                    common.set_meta(conn, "last_alert_at", now)
                    common.set_meta(conn, "last_alert_key", alert_key)
                else:
                    notification_error = detail
                    if status["done"]:
                        conn.execute("DELETE FROM meta WHERE key='done_pending'")
                        conn.commit()
        finally:
            common.render_dashboard(cfg, conn)
        state = "done" if status["done"] else "alert" if status["problems"] else "ok"
        line = (
            f"MAILSHOT_HEALTH {state} queued={status['queued']} "
            f"sent={status['sent']} total={status['total']}"
        )
        if status["paused"]:
            line += " paused=1"
        if status["problems"] and not status["done"]:
            line += " problems=" + json.dumps(status["problems"], ensure_ascii=False)
        if notification_error is not None:
            line += " notify_error=" + json.dumps(str(notification_error), ensure_ascii=False)
        print(line)
    except Exception as exc:
        print("MAILSHOT_HEALTH error=" + json.dumps(str(exc), ensure_ascii=False))
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
