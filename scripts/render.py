"""Generate the self-contained dashboard at web/index.html."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common


def local_time(value: str | None, tz_name: str = "Europe/Prague") -> str:
    if not value:
        return "—"
    try:
        stamp = datetime.strptime(value, common.TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return "—"
    return stamp.astimezone(ZoneInfo(tz_name)).strftime("%d.%m.%Y %H:%M")


def build_payload(conn, cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else common.load_config()
    counts = conn.execute(
        "SELECT COUNT(*) AS total,"
        " COALESCE(SUM(status='sent'),0) AS sent,"
        " COALESCE(SUM(status='queued'),0) AS queued,"
        " COALESCE(SUM(status='failed'),0) AS failed,"
        " COALESCE(SUM(status='bounced'),0) AS bounced,"
        " COALESCE(SUM(replied_at IS NOT NULL),0) AS replied"
        " FROM recipients"
    ).fetchone()
    sent_24h = common.sent_last_24h(conn)
    recent = [
        dict(row)
        for row in conn.execute(
            "SELECT p.gmail_id, p.subject, p.snippet, p.received_at, p.kind,"
            " p.from_addr, COALESCE(r.office,'') AS office, COALESCE(r.obec,'') AS obec"
            " FROM replies p LEFT JOIN recipients r ON r.email = p.recipient_email"
            " ORDER BY p.id DESC LIMIT 20"
        )
    ]
    rows = [
        [
            row["id"],
            row["office"] or "",
            row["obec"] or "",
            row["kraj"] or "",
            round(row["distance_km"] or 0, 2),
            row["email"],
            "replied" if row["replied_at"] else row["status"],
            row["sent_at"] or "",
            row["replied_at"] or "",
        ]
        for row in conn.execute(
            "SELECT * FROM recipients ORDER BY distance_km ASC, id ASC"
        )
    ]
    lock = Path(cfg["db_path"]).parent / "send.lock"
    last_watch_at = common.get_meta(conn, "last_watch_at")
    last_send_at = common.get_meta(conn, "last_send_at")
    done_at = common.get_meta(conn, "done_at")
    paused = common.paused_info(cfg)
    tz = cfg.get("timezone") or "Europe/Prague"
    return {
        "generated_at": datetime.now(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M:%S"),
        "timezone": tz,
        "paused": paused["active"],
        "paused_since": paused["since"],
        "paused_reason": paused["reason"],
        "last_watch_at": last_watch_at,
        "last_send_at": last_send_at,
        "done_at": done_at,
        "watch_ended_at": common.get_meta(conn, "watch_ended_at"),
        "last_watch_at_display": local_time(last_watch_at, tz),
        "last_send_at_display": local_time(last_send_at, tz),
        "done_at_display": local_time(done_at, tz),
        "watch_ended_at_display": local_time(common.get_meta(conn, "watch_ended_at"), tz),
        "online_stale_minutes": cfg.get("health", {}).get("online_stale_minutes", 35),
        "done": bool(done_at) or (counts["total"] > 0 and counts["queued"] == 0),
        "total": counts["total"],
        "sent": counts["sent"],
        "queued": counts["queued"],
        "failed": counts["failed"],
        "bounced": counts["bounced"],
        "replied": counts["replied"],
        "sent_24h": sent_24h,
        "daily_cap": cfg["daily_cap"],
        "sending": lock.exists(),
        "recent": recent,
        "rows": rows,
    }


def write_dashboard(cfg: dict, conn) -> Path:
    payload = build_payload(conn, cfg)
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("{{DATA}}", data)
    path = Path(cfg["dashboard_path"])
    common.atomic_write(path, html)
    return path


TEMPLATE = """<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>Matriky · hromadné oslovení</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0b0d12;color:#e5e7eb;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:#8b93a7;font-size:13px}
.banner{display:none;margin:18px 0 0;padding:13px 17px;border-radius:12px;background:#3a2a10;border:1px solid #7c5a12;color:#fbbf24;gap:12px;align-items:baseline;font-size:13px}
.banner.on{display:flex}
.banner b{font-size:12.5px;text-transform:uppercase;letter-spacing:.09em;color:#fcd34d;white-space:nowrap}
.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin:22px 0 12px}
.card{background:#141821;border:1px solid #232a38;border-radius:12px;padding:15px 17px}
.card .label{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:#8b93a7}
.card .value{font-size:27px;font-weight:700;margin-top:5px}
.card .hint{font-size:12px;color:#8b93a7;margin-top:2px}
.progress{background:#141821;border:1px solid #232a38;border-radius:12px;padding:15px 17px;margin-bottom:18px}
.bar{height:9px;background:#1d2432;border-radius:99px;overflow:hidden;margin-top:9px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#38bdf8,#22c55e);width:0}
.meta{display:flex;justify-content:space-between;gap:12px;font-size:12px;color:#8b93a7;margin-top:8px}
.panel{background:#141821;border:1px solid #232a38;border-radius:12px;margin-bottom:18px;overflow:hidden}
.panel h2{font-size:14px;margin:0;padding:14px 17px;border-bottom:1px solid #232a38;color:#cbd5e1;font-weight:600}
.replies{padding:6px 17px 14px}
.reply{padding:10px 0;border-bottom:1px solid #1d2432}
.reply:last-child{border-bottom:0}
.reply .top{display:flex;justify-content:space-between;gap:10px;font-size:13px}
.reply .who{font-weight:600;color:#e5e7eb}
.reply .when{color:#8b93a7;white-space:nowrap;font-size:12px}
.reply .snippet{color:#9aa4b8;font-size:12.5px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;padding:12px 17px;border-bottom:1px solid #232a38;align-items:center}
.toolbar input{background:#0f1420;border:1px solid #2a3242;color:#e5e7eb;border-radius:8px;padding:8px 11px;font-size:13px;min-width:240px;flex:1}
button{background:#0f1420;border:1px solid #2a3242;color:#cbd5e1;border-radius:8px;padding:7px 12px;font-size:12.5px;cursor:pointer}
button.active{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
.tblwrap{max-height:62vh;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{position:sticky;top:0;background:#101522;color:#8b93a7;text-align:left;font-weight:600;padding:9px 12px;border-bottom:1px solid #232a38;white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid #171d29;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:320px}
tr:hover td{background:#171d29}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600}
.p-queued{background:#1f2735;color:#94a3b8}
.p-sent{background:#12294a;color:#60a5fa}
.p-replied{background:#123524;color:#4ade80}
.p-bounced{background:#3a1616;color:#f87171}
.p-failed{background:#3a2a10;color:#fbbf24}
.empty{padding:18px 17px;color:#8b93a7}
@media(max-width:860px){.cards{grid-template-columns:repeat(2,1fr)}td:nth-child(3),th:nth-child(3),td:nth-child(4),th:nth-child(4){display:none}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Matriky · hromadné oslovení</h1>
  <div class="sub" id="sub"></div>
  <div class="banner" id="banner"><b>⏸ Pozastaveno</b><span id="banner-text"></span></div>

  <div class="cards">
    <div class="card"><div class="label">Rozesílá</div><div class="value" id="k-sent"></div><div class="hint" id="k-sent-hint"></div></div>
    <div class="card"><div class="label">Stav</div><div class="value" id="k-status"></div><div class="hint" id="k-status-hint"></div><div class="hint" id="k-send-hint"></div></div>
    <div class="card"><div class="label">Zbývá</div><div class="value" id="k-queued"></div><div class="hint">ve frontě</div></div>
    <div class="card"><div class="label">Odpovědělo</div><div class="value" id="k-replied"></div><div class="hint">z odeslaných</div></div>
    <div class="card"><div class="label">Nedoručeno</div><div class="value" id="k-bounced"></div><div class="hint" id="k-failed-hint"></div></div>
  </div>

  <div class="progress">
    <div class="meta"><span id="p-label"></span><span id="p-quota"></span></div>
    <div class="bar"><i id="p-bar"></i></div>
    <div class="meta"><span id="p-state"></span><span id="p-generated"></span></div>
  </div>

  <div class="panel">
    <h2>Poslední odpovědi</h2>
    <div class="replies" id="replies"></div>
  </div>

  <div class="panel">
    <h2>Všechny matriky</h2>
    <div class="toolbar">
      <input id="search" placeholder="Hledat úřad, obec, e-mail…">
      <button data-f="all" class="active">Vše</button>
      <button data-f="queued">Čeká</button>
      <button data-f="sent">Odesláno</button>
      <button data-f="replied">Odpovědělo</button>
      <button data-f="bounced">Nedoručeno</button>
      <button data-f="failed">Chyba</button>
    </div>
    <div class="tblwrap">
      <table>
        <thead><tr><th>#</th><th>Úřad</th><th>Obec</th><th>Kraj</th><th>km</th><th>E-mail</th><th>Stav</th><th>Odesláno</th><th>Odpověď</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
const DATA = {{DATA}};
const LABELS = {queued:'čeká', sent:'odesláno', replied:'odpověděl', bounced:'nedoručeno', failed:'chyba'};
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const shortTime = (value) => value ? value.slice(0, 16).replace(' ', ' · ') : '—';
const watchTime = DATA.last_watch_at ? new Date(DATA.last_watch_at.replace(' ', 'T') + 'Z').getTime() : NaN;
DATA.online = Number.isFinite(watchTime) && Date.now() - watchTime < DATA.online_stale_minutes * 60000;
const statusCard = document.getElementById('k-status');
const watchEnded = !!DATA.watch_ended_at && !DATA.done_at;
statusCard.textContent = DATA.done_at ? 'dokončeno' : watchEnded ? '● ukončeno' : DATA.online ? '● online' : '● offline';
statusCard.style.color = DATA.done_at || DATA.online && !watchEnded ? '#4ade80' : watchEnded ? '#94a3b8' : '#f87171';
document.getElementById('k-status-hint').textContent = DATA.done_at
  ? 'dokončeno ' + DATA.done_at_display : watchEnded
  ? 'watcher ukončen ' + DATA.watch_ended_at_display
  : 'naposledy ' + DATA.last_watch_at_display;
document.getElementById('k-send-hint').textContent = DATA.last_send_at
  ? 'odesílání ' + DATA.last_send_at_display : '';

const banner = document.getElementById('banner');
if (DATA.paused) {
  banner.classList.add('on');
  document.getElementById('banner-text').textContent =
    (DATA.paused_reason || 'Odesílání dalších dávek je zastavené.') +
    (DATA.paused_since ? ' (od ' + DATA.paused_since + ')' : '');
}

document.getElementById('sub').textContent =
  DATA.total + ' matričních úřadů · ' + DATA.sent + ' odesláno · ' + DATA.replied + ' odpovědí';
document.getElementById('k-sent').textContent = DATA.sent + ' / ' + DATA.total;
document.getElementById('k-sent-hint').textContent = Math.round(DATA.sent / Math.max(DATA.total, 1) * 100) + ' % seznamu';
document.getElementById('k-queued').textContent = DATA.queued;
document.getElementById('k-replied').textContent = DATA.replied;
document.getElementById('k-bounced').textContent = DATA.bounced;
document.getElementById('k-failed-hint').textContent = DATA.failed ? DATA.failed + ' chyb' : 'bez chyb';
document.getElementById('p-label').textContent = DATA.queued === 0 && DATA.total > 0 && DATA.sent > 0 ? 'Vše rozesláno' : 'Průběh: ' + DATA.sent + ' z ' + DATA.total;
document.getElementById('p-quota').textContent = 'Posledních 24 h: ' + DATA.sent_24h + ' / ' + DATA.daily_cap;
document.getElementById('p-bar').style.width = Math.min(100, DATA.sent / Math.max(DATA.total, 1) * 100) + '%';
document.getElementById('p-state').textContent = DATA.sending ? '● dávka právě běží' : DATA.paused ? '● pozastaveno' : '● připraveno';
document.getElementById('p-state').style.color = DATA.paused && !DATA.sending ? '#fbbf24' : '';
document.getElementById('p-generated').textContent = 'Vygenerováno ' + DATA.generated_at;

const replies = document.getElementById('replies');
if (!DATA.recent.length) {
  replies.innerHTML = '<div class="empty">Zatím žádná odpověď.</div>';
} else {
  replies.innerHTML = DATA.recent.map((item) => (
    '<div class="reply"><div class="top"><span class="who">' + esc(item.office || item.from_addr) +
    '</span><span class="when">' + esc(shortTime(item.received_at)) + '</span></div>' +
    '<div class="snippet">' + esc(item.subject || '') + ' — ' + esc((item.snippet || '').slice(0, 180)) + '</div></div>'
  )).join('');
}

let filter = 'all';
const search = document.getElementById('search');
const tbody = document.getElementById('tbody');

function render() {
  const query = search.value.trim().toLowerCase();
  const out = [];
  let shown = 0;
  for (const row of DATA.rows) {
    const status = row[6];
    if (filter !== 'all' && status !== filter) continue;
    if (query) {
      const haystack = (row[1] + ' ' + row[2] + ' ' + row[3] + ' ' + row[5]).toLowerCase();
      if (!haystack.includes(query)) continue;
    }
    shown += 1;
    if (shown > 400) continue;
    out.push('<tr><td>' + row[0] + '</td><td title="' + esc(row[1]) + '">' + esc(row[1]) +
      '</td><td>' + esc(row[2]) + '</td><td>' + esc(row[3]) + '</td><td>' + row[4] +
      '</td><td title="' + esc(row[5]) + '">' + esc(row[5]) + '</td><td><span class="pill p-' + status + '">' +
      LABELS[status] + '</span></td><td>' + esc(shortTime(row[7])) + '</td><td>' + esc(shortTime(row[8])) + '</td></tr>');
  }
  tbody.innerHTML = out.join('') || '<tr><td colspan="9" class="empty">Nic nenalezeno.</td></tr>';
}

document.querySelectorAll('button[data-f]').forEach((button) => {
  button.addEventListener('click', () => {
    filter = button.dataset.f;
    document.querySelectorAll('button[data-f]').forEach((other) => other.classList.toggle('active', other === button));
    render();
  });
});
search.addEventListener('input', render);
render();
</script>
</body>
</html>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the mailshot dashboard.")
    parser.add_argument("--config", help="path to config.json")
    args = parser.parse_args(argv)
    cfg = common.load_config(args.config)
    conn = common.connect(cfg)
    try:
        write_dashboard(cfg, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

