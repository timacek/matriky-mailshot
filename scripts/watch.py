"""Watch Gmail for replies and bounces, label them, notify the operator, refresh dashboard."""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common

HEADERS = [
    "From",
    "To",
    "Subject",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
    "X-Failed-Recipients",
    "Auto-Submitted",
]
QUERY = "newer_than:2d (in:inbox OR in:spam)"


def strip_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value or "")
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def header_map(message: dict) -> dict:
    headers = message.get("payload", {}).get("headers", [])
    return {item["name"].lower(): item.get("value", "") for item in headers if item.get("name")}


def classify(
    headers: dict,
    recipients: set[str],
    our_message_ids: set[str],
    our_thread_ids: set[str],
    our_email: str,
    thread_id: str | None = None,
    subject_mark: str = "",
) -> dict | None:
    """Decide whether an inbox message is a reply / autoreply / bounce we care about."""
    sender_email = ""
    if headers.get("from"):
        # parseaddr keeps the address even when the display name has commas.
        from email.utils import parseaddr

        sender_email = parseaddr(headers["from"])[1].strip().lower()
    if not sender_email or sender_email == our_email.lower():
        return None

    failed = (headers.get("x-failed-recipients") or "").strip()
    if failed or "mailer-daemon" in sender_email or "postmaster" in sender_email:
        target = failed.split(",")[0].strip().lower() if failed else ""
        return {"kind": "bounce", "recipient_email": target, "from_addr": sender_email}

    references = f"{headers.get('references', '')} {headers.get('in-reply-to', '')}"
    by_reference = any(mid and mid in references for mid in our_message_ids)
    by_thread = bool(thread_id and thread_id in our_thread_ids)
    by_sender = sender_email in recipients
    mark = strip_diacritics(subject_mark).lower()
    by_subject = bool(mark) and mark in strip_diacritics(headers.get("subject", "")).lower()

    if not (by_reference or by_thread or by_sender or by_subject):
        return None

    kind = "autoreply" if "auto" in headers.get("auto-submitted", "").lower() else "reply"
    return {
        "kind": kind,
        "recipient_email": sender_email if by_sender else "",
        "from_addr": sender_email,
    }


def fetch_metadata(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=HEADERS)
        .execute()
    )


def notification_text(reply, office: str) -> str:
    snippet = " ".join((reply["snippet"] or "").split())[:200]
    lines = [f"📬 {office or reply['from_addr']}", reply["subject"] or ""]
    if snippet:
        lines.append(snippet)
    kind = {"reply": "odpověď", "autoreply": "automatická odpověď", "bounce": "NEDORUČENO"}[
        reply["kind"]
    ]
    lines.append(f"[{kind}] https://mail.google.com/mail/u/0/#inbox/{reply['gmail_id']}")
    return "\n".join(line for line in lines if line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch the matrika inbox.")
    parser.add_argument("--config", help="path to config.json")
    args = parser.parse_args(argv)

    cfg = common.load_config(args.config)
    conn = common.connect(cfg)
    if datetime.now().strftime("%Y-%m-%d") > cfg["watch_until"]:
        common.set_meta(conn, "last_watch_at", common.utcnow())
        common.set_meta(conn, "watch_ended_at", common.utcnow())
        try:
            common.render_dashboard(cfg, conn)
        except Exception as exc:  # dashboard must never break the watcher
            print(f"MAILSHOT_WATCH dashboard_error={exc}")
        print(f"WATCH_ENDED watch_until={cfg['watch_until']}")
        return 0

    service = common.gmail_service(cfg)
    label_id = common.ensure_label(service, conn, cfg["label_name"])

    recipients = {
        row["email"] for row in conn.execute("SELECT email FROM recipients").fetchall()
    }
    our_ids = {
        row["message_id"]
        for row in conn.execute("SELECT message_id FROM mail").fetchall()
        if row["message_id"]
    }
    our_threads = {
        row["thread_id"]
        for row in conn.execute("SELECT thread_id FROM mail").fetchall()
        if row["thread_id"]
    }
    thread_map = {
        row["thread_id"]: row["email"]
        for row in conn.execute(
            "SELECT m.thread_id, r.email FROM mail m JOIN recipients r ON r.id = m.recipient_id"
        )
        if row["thread_id"]
    }
    id_map = {
        row["message_id"]: row["email"]
        for row in conn.execute(
            "SELECT m.message_id, r.email FROM mail m JOIN recipients r ON r.id = m.recipient_id"
        )
        if row["message_id"]
    }

    listing = (
        service.users()
        .messages()
        .list(userId="me", q=QUERY, maxResults=200)
        .execute()
    )
    new_count = replies = bounces = 0

    for meta in listing.get("messages", []):
        message = fetch_metadata(service, meta["id"])
        headers = header_map(message)
        info = classify(
            headers,
            recipients,
            our_ids,
            our_threads,
            cfg["from_email"],
            thread_id=message.get("threadId"),
            subject_mark=cfg.get("subject_mark", ""),
        )
        if not info:
            continue

        received = datetime.fromtimestamp(
            int(message.get("internalDate", "0")) / 1000, timezone.utc
        ).strftime(common.TS_FMT)
        cursor = conn.execute(
            "INSERT OR IGNORE INTO replies"
            "(gmail_id, recipient_email, from_addr, subject, snippet, received_at, kind)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                message["id"],
                info["recipient_email"],
                info["from_addr"],
                headers.get("subject", ""),
                message.get("snippet", ""),
                received,
                info["kind"],
            ),
        )
        if cursor.rowcount == 0:
            continue
        new_count += 1

        service.users().messages().modify(
            userId="me", id=message["id"], body={"addLabelIds": [label_id]}
        ).execute()

        target = info["recipient_email"]
        references = f"{headers.get('references', '')} {headers.get('in-reply-to', '')}"
        if not target and message.get("threadId") in thread_map:
            target = thread_map[message["threadId"]]
        if not target:
            for candidate in our_ids:
                if candidate in references:
                    target = id_map.get(candidate, "")
                    break

        if info["kind"] == "bounce":
            bounces += 1
            if target:
                conn.execute(
                    "UPDATE recipients SET status='bounced', error=? WHERE email=?",
                    (f"bounce {received}", target),
                )
        else:
            replies += 1
            if target:
                conn.execute(
                    "UPDATE recipients SET replied_at=COALESCE(replied_at, ?) WHERE email=?",
                    (received, target),
                )
        conn.commit()

    notified = failed_notifications = 0
    offices = {
        row["email"]: row["office"]
        for row in conn.execute("SELECT email, office FROM recipients")
    }
    for reply in conn.execute("SELECT * FROM replies WHERE notified=0 ORDER BY id").fetchall():
        office = offices.get(reply["recipient_email"] or "", "")
        ok, detail = common.send_notification(cfg, notification_text(reply, office))
        if ok:
            conn.execute("UPDATE replies SET notified=1 WHERE id=?", (reply["id"],))
            conn.commit()
            notified += 1
        else:
            failed_notifications += 1
            common.log_json(cfg, event="notify_failed", reply_id=reply["id"], detail=detail)
            break

    common.set_meta(conn, "last_watch_at", common.utcnow())
    try:
        common.render_dashboard(cfg, conn)
    except Exception as exc:  # dashboard must never break the watcher
        print(f"MAILSHOT_WATCH dashboard_error={exc}")

    print(
        f"MAILSHOT_WATCH new={new_count} replies={replies} bounces={bounces} "
        f"notified={notified} notify_failed={failed_notifications} label={cfg['label_name']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
