"""Shared helpers for the registry-office mailshot (prepare / send / watch / render)."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"

# Where the OAuth token lives when config.json leaves token_path empty.
CONFIG_SUBDIR = "matriky-mailshot"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# SQLite strftime gives "YYYY-MM-DD HH:MM:SS" (UTC) — every timestamp we store
# uses the same shape so lexicographic comparison against datetime('now', ...) works.
TS_FMT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipients(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
  office TEXT, obec TEXT, kraj TEXT, code TEXT,
  distance_km REAL, lat REAL, lon REAL,
  status TEXT NOT NULL DEFAULT 'queued',
  sent_at TEXT, replied_at TEXT, message_id TEXT, thread_id TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS mail(
  id INTEGER PRIMARY KEY, recipient_id INTEGER NOT NULL,
  message_id TEXT UNIQUE, thread_id TEXT, subject TEXT,
  sent_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'sent', error TEXT);
CREATE TABLE IF NOT EXISTS replies(
  id INTEGER PRIMARY KEY, gmail_id TEXT UNIQUE NOT NULL, recipient_email TEXT,
  from_addr TEXT, subject TEXT, snippet TEXT, received_at TEXT,
  kind TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_recipients_status ON recipients(status);
CREATE INDEX IF NOT EXISTS idx_mail_sent_at ON mail(sent_at);
"""

PATH_KEYS = (
    "source_list",
    "db_path",
    "dashboard_path",
    "log_path",
    "templates_dir",
    "credentials_path",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(TS_FMT)


def config_home() -> Path:
    """Directory for the OAuth token; MAILSHOT_CONFIG_HOME overrides it (tests, CI)."""
    env = os.environ.get("MAILSHOT_CONFIG_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / CONFIG_SUBDIR


def token_path(cfg: dict) -> Path:
    custom = str(cfg.get("token_path") or "").strip()
    return Path(custom) if custom else config_home() / "token.json"


def load_config_dict(cfg: dict) -> dict:
    """Resolve the relative path keys against the repo root (in place, returns cfg)."""
    for key in PATH_KEYS:
        if cfg.get(key):
            value = Path(str(cfg[key]))
            cfg[key] = str(value if value.is_absolute() else (ROOT / value))
    return cfg


def load_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    return load_config_dict(json.loads(cfg_path.read_text(encoding="utf-8")))


def connect(cfg: dict) -> sqlite3.Connection:
    db_path = Path(cfg["db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def paused_info(cfg: dict) -> dict:
    """Manual send switch from config.json: {'active': bool, 'since': str, 'reason': str}."""
    block = cfg.get("paused") or {}
    return {
        "active": bool(block.get("active")),
        "since": str(block.get("since") or ""),
        "reason": str(block.get("reason") or ""),
    }


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def sent_last_24h(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM mail WHERE sent_at >= datetime('now','-24 hours')"
    ).fetchone()[0]


def log_json(cfg: dict, **fields) -> None:
    path = Path(cfg["log_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": utcnow(), **fields}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def gmail_service(cfg: dict | None = None):
    """Build a Gmail client from the OAuth token written by scripts/auth.py."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    cfg = cfg if cfg is not None else load_config()
    path = token_path(cfg)
    if not path.is_file():
        raise SystemExit(f"Chybí token {path} — spusť: python scripts/auth.py")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise SystemExit(f"Token {path} vypršel — spusť znovu: python scripts/auth.py")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def ensure_label(service, conn: sqlite3.Connection, name: str) -> str:
    """Return the Gmail label id for `name`, creating the label when missing."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == name:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('label_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (label["id"],),
            )
            conn.commit()
            return label["id"]
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('label_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (created["id"],),
    )
    conn.commit()
    return created["id"]


def render_templates(cfg: dict, office: str, obec: str, date_cs: str) -> tuple[str, str]:
    """Fill email.html/email.txt: per-recipient {{URAD}}/{{OBEC}}/{{DATE}} plus config placeholders."""
    templates = Path(cfg["templates_dir"])
    html = (templates / "email.html").read_text(encoding="utf-8")
    text = (templates / "email.txt").read_text(encoding="utf-8")
    values = {"{{URAD}}": office or "", "{{OBEC}}": obec or "", "{{DATE}}": date_cs}
    for key, value in (cfg.get("placeholders") or {}).items():
        values["{{" + str(key).strip("{} ") + "}}"] = str(value)
    for key, value in values.items():
        html = html.replace(key, value)
        text = text.replace(key, value)
    return html, text


def build_mime(
    cfg: dict,
    to_email: str,
    subject: str,
    html: str,
    text: str,
    message_id: str | None = None,
) -> tuple[EmailMessage, str]:
    """Multipart/alternative message plus its base64url raw form for the Gmail API."""
    msg = EmailMessage(policy=SMTP)
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to_email
    msg["Reply-To"] = cfg["reply_to"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id or make_msgid(domain="gmail.com")
    msg.set_content(text, subtype="plain", charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return msg, raw


def send_notification(cfg: dict, text: str) -> tuple[bool, str]:
    """Notify the operator. notify.type: none (default) | stdout | webhook."""
    block = cfg.get("notify") or {}
    kind = str(block.get("type") or "none").lower()
    if kind == "none":
        return True, "disabled"
    if kind == "stdout":
        print(text)
        return True, "stdout"
    if kind != "webhook":
        return False, f"neznámý notify.type={kind!r}"
    url = str(block.get("webhook_url") or "").strip()
    if not url:
        return False, "notify.webhook_url chybí"
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", "replace")
            return 200 <= response.status < 300, body[:300]
    except urllib.error.URLError as exc:
        return False, str(exc)


def acquire_lock(path: str | Path, stale_seconds: int = 900) -> bool:
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 0
        if age > stale_seconds:
            try:
                lock.unlink()
            except OSError:
                pass
            return acquire_lock(lock, stale_seconds)
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    return True


def release_lock(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def atomic_write(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


def render_dashboard(cfg: dict, conn: sqlite3.Connection) -> Path:
    import render  # noqa: PLC0415  (render imports common -> lazy on purpose)

    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    return render.write_dashboard(cfg, conn)
