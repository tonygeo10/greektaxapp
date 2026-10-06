#!/usr/bin/env python3
"""Greek accounting news digest.

Pipeline: fetch -> filter by topic -> extract deadlines from the source text
-> dedupe -> human review -> digest -> deliver (text / JSON / Telegram).

Design rule: nothing is generated. Every field comes from the source item,
and every deadline carries the exact text snippet it was read from.
Standard library only.
"""
import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = os.environ.get("DIGEST_DB", str(HERE / "digest.db"))
SOURCES_PATH = os.environ.get("DIGEST_SOURCES", str(HERE / "sources.json"))
UA = "Mozilla/5.0 (compatible; AccountingDigestBot/1.0)"
WEB = HERE / "web"
WEB_MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8"}

# ---------------------------------------------------------------- text utils


def norm(s):
    """Lowercase, strip Greek accents, fold final sigma. Length-preserving for NFC input."""
    s = unicodedata.normalize("NFD", unicodedata.normalize("NFC", s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("ς", "σ")


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return " ".join(html.unescape(s).split())


# ------------------------------------------------------------ classification

TAGS = {
    "ΦΠΑ": ["φπα"],
    "ΕΦΚΑ / Ασφαλιστικά": ["εφκα", "ασφαλιστικ", "εισφορ"],
    "Μισθοδοσία": ["μισθοδοσ", "εργανη", "ε4", "ε5"],
    "myDATA / Τιμολόγηση": ["mydata", "τιμολογ"],
    "Φορολογία εισοδήματος": ["φορολογικ", "δηλωση", "δηλωσεις", "ε1", "ε2", "ε3", "εισοδημα", "εκκαθαριστικ"],
    "Ακίνητα / ΕΝΦΙΑ": ["ενφια", "ακινητ"],
    "ΑΑΔΕ": ["ααδε"],
    "Παράταση / Προθεσμία": ["παραταση", "προθεσμι"],
    "Πρόστιμα": ["προστιμ"],
}
TAG_RES = {t: [re.compile(r"(?<!\w)" + re.escape(k)) for k in ks] for t, ks in TAGS.items()}


def classify(text):
    t = norm(text)
    return [tag for tag, res in TAG_RES.items() if any(r.search(t) for r in res)]


# --------------------------------------------------------- deadline extraction

_STEMS = [("ιανουαρι", 1), ("φεβρουαρι", 2), ("μαρτι", 3), ("απριλι", 4), ("μαι", 5), ("ιουνι", 6),
          ("ιουλι", 7), ("αυγουστ", 8), ("σεπτεμβρι", 9), ("οκτωβρι", 10), ("νοεμβρι", 11), ("δεκεμβρι", 12)]
MONTH_WORDS = {stem + end: n for stem, n in _STEMS for end in ("ου", "οσ")}
DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTH_WORDS) + r")(?:\s+(\d{4}))?")
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)")
CUE = re.compile(r"(εωσ|μεχρι|προθεσμι|παραταση|παρατεινεται|εντοσ|ληγει|ληξη|υποβολη)")


def find_deadline(text, published=None):
    """Return {'date','evidence','year_inferred'} for the first date that follows a deadline cue word."""
    text = unicodedata.normalize("NFC", text or "")
    t = norm(text)
    cands = []
    for m in DATE_TXT.finditer(t):
        cands.append((m.start(), m.end(), int(m.group(1)), MONTH_WORDS[m.group(2)],
                      int(m.group(3)) if m.group(3) else None))
    for m in DATE_NUM.finditer(t):
        cands.append((m.start(), m.end(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    base = published or date.today()
    for start, end, d, mon, y in sorted(cands):
        if not CUE.search(t[max(0, start - 45):start]):
            continue
        inferred = y is None
        try:
            dl = date(y or base.year, mon, d)
            if inferred and dl < base - timedelta(days=30):
                dl = date(base.year + 1, mon, d)
        except ValueError:
            continue
        evidence = " ".join(text[max(0, start - 45):end].split())
        return {"date": dl.isoformat(), "evidence": evidence, "year_inferred": inferred}
    return None


# ------------------------------------------------------------------- fetching


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() if r.headers else None
    return raw.decode(charset or "utf-8", errors="replace")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_feed(text):
    root = ET.fromstring(text)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d = {_local(c.tag): c for c in el}
        title = (d["title"].text or "").strip() if "title" in d else ""
        link = ""
        if "link" in d:
            link = (d["link"].get("href") or d["link"].text or "").strip()
        desc_el = next((d[k] for k in ("description", "summary", "encoded", "content") if k in d), None)
        date_el = next((d[k] for k in ("pubDate", "published", "updated", "date") if k in d), None)
        items.append({
            "title": " ".join(title.split()),
            "link": link,
            "description": strip_html(desc_el.text) if desc_el is not None and desc_el.text else "",
            "published": parse_date(date_el.text) if date_el is not None else None,
        })
    return items


class _LinkParser(HTMLParser):
    def __init__(self, base, pattern):
        super().__init__()
        self.base, self.pattern, self.cur, self.buf, self.items = base, pattern, None, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.cur, self.buf = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self.cur is not None:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.cur:
            title = " ".join("".join(self.buf).split())
            url = urllib.parse.urljoin(self.base, self.cur)
            if len(title) > 15 and re.search(self.pattern, url):
                self.items.append({"title": title, "link": url, "description": "", "published": None})
            self.cur = None


def parse_links(body, base, pattern):
    p = _LinkParser(base, pattern or "")
    p.feed(body)
    return p.items


def collect(src):
    body = http_get(src["url"])
    if src["type"] == "rss":
        return parse_feed(body)
    if src["type"] == "html_links":
        items = parse_links(body, src["url"], src.get("link_pattern", ""))[: src.get("max_items", 15)]
        pat = src.get("url_date_pattern")  # regex with 3 groups: day, month, year (e.g. ...-02102026)
        if pat:
            for it in items:
                m = re.search(pat, it["link"])
                if m:
                    try:
                        it["published"] = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    except ValueError:
                        pass
        return items
    raise ValueError(f"unknown source type {src['type']!r}")


# -------------------------------------------------------------------- storage

SCHEMA = """CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT UNIQUE, published TEXT, fetched_at TEXT,
  excerpt TEXT, tags TEXT, deadline TEXT, deadline_evidence TEXT, year_inferred INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending', sent_at TEXT)"""


def db(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def load_sources(path=None):
    with open(path or SOURCES_PATH, encoding="utf-8") as f:
        return [s for s in json.load(f)["sources"] if s.get("enabled", True)]


def ingest(conn, sources, today=None, auto_approve=False):
    today = today or date.today()
    report = []
    for src in sources:
        r = {"source": src["name"], "found": 0, "new": 0, "error": None}
        report.append(r)
        try:
            items = collect(src)
        except Exception as e:  # one broken source must not stop the others
            r["error"] = f"{type(e).__name__}: {e}"
            continue
        r["found"] = len(items)
        for it in items:
            if not it["link"] or not it["title"]:
                continue
            pub = it["published"]
            calendar = src.get("pubdate_is_deadline")  # e.g. Taxheaven calendar: pubDate is the event date
            event = None
            if calendar:
                event, pub = pub, None
                if not event or event < today or (event - today).days > src.get("max_ahead_days", 45):
                    continue
            elif pub and (today - pub).days > src.get("max_age_days", 14):
                continue
            text = f"{it['title']} {it['description']}"
            tags = classify(text)
            if not tags and not src.get("keep_all"):
                continue
            # Recurring calendar events share titles month to month, so they dedupe on URL only.
            q = "SELECT 1 FROM items WHERE url=?" + ("" if calendar else " OR title=?")
            if conn.execute(q, (it["link"],) if calendar else (it["link"], it["title"])).fetchone():
                continue
            if calendar:
                dl = {"date": event.isoformat(), "year_inferred": False,
                      "evidence": f"{src['name']}: {event:%d/%m/%Y}"}
            else:
                dl = find_deadline(text, pub or today)
            conn.execute(
                "INSERT INTO items(id,source,title,url,published,fetched_at,excerpt,tags,deadline,"
                "deadline_evidence,year_inferred,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (hashlib.sha1(it["link"].encode()).hexdigest()[:10], src["name"], it["title"], it["link"],
                 pub.isoformat() if pub else None, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 it["description"][:300] if src.get("store_excerpt", True) else "",
                 ", ".join(tags), dl["date"] if dl else None, dl["evidence"] if dl else None,
                 int(dl["year_inferred"]) if dl else 0, "approved" if auto_approve else "pending"))
            r["new"] += 1
    conn.commit()
    return report


# --------------------------------------------------------------------- review


def pending_rows(conn):
    cols = "id,source,title,url,published,excerpt,tags,deadline,deadline_evidence,year_inferred"
    return [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM items WHERE status='pending' ORDER BY COALESCE(published,'') DESC, fetched_at DESC")]


def set_status(conn, ids, status):
    if status not in ("approved", "rejected", "pending"):
        raise ValueError(f"bad status {status!r}")
    n = sum(conn.execute("UPDATE items SET status=? WHERE id=?", (status, i)).rowcount for i in ids)
    conn.commit()
    return n


def set_deadline(conn, item_id, iso):
    """Human correction of an extracted deadline. iso=None clears it. Raises ValueError on a bad date."""
    if iso is None:
        n = conn.execute("UPDATE items SET deadline=NULL, deadline_evidence=NULL, year_inferred=0 WHERE id=?",
                         (item_id,)).rowcount
    else:
        date.fromisoformat(iso)
        n = conn.execute(
            "UPDATE items SET deadline=?, year_inferred=0, "
            "deadline_evidence=COALESCE(deadline_evidence,'Ορίστηκε χειροκίνητα') WHERE id=?",
            (iso, item_id)).rowcount
    conn.commit()
    return n


# --------------------------------------------------------------------- digest


def days_left(row, today):
    return (date.fromisoformat(row["deadline"]) - today).days


def build(conn, today=None, days=2, only_new=False):
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE status='approved' AND "
        "(COALESCE(published, substr(fetched_at,1,10)) >= ? OR deadline >= ?)",
        (cutoff, today.isoformat()))]
    deadlines, news = [], []
    for r in rows:
        if r["deadline"] and r["deadline"] >= today.isoformat():
            if not only_new or r["sent_at"] is None or days_left(r, today) <= 7:
                deadlines.append(r)
        elif not only_new or r["sent_at"] is None:
            news.append(r)
    deadlines.sort(key=lambda r: r["deadline"])
    news.sort(key=lambda r: r["published"] or "", reverse=True)
    return deadlines, news


def render_text(deadlines, news, today=None):
    today = today or date.today()
    out = [f"📋 Λογιστικό Δελτίο — {today:%d/%m/%Y}"]
    if deadlines:
        out.append("\n⏰ ΠΡΟΘΕΣΜΙΕΣ")
        for r in deadlines:
            n = days_left(r, today)
            icon = "🔴" if n <= 3 else "🟠" if n <= 10 else "🟡"
            when = "σήμερα" if n == 0 else f"σε {n} ημέρες"
            dl = date.fromisoformat(r["deadline"])
            line = f"\n{icon} {dl:%d/%m/%Y} ({when}) — {r['title']}"
            line += f"\n   Πηγή: {r['source']} · {r['url']}"
            line += f"\n   Από το κείμενο: «{r['deadline_evidence']}»"
            if r["year_inferred"]:
                line += " (το έτος υποτέθηκε, ελέγξτε)"
            out.append(line)
    if news:
        out.append("\n📰 ΝΕΑ")
        for r in news:
            out.append(f"\n• {r['title']}\n   [{r['tags']}] {r['source']} · {r['url']}")
    if not deadlines and not news:
        out.append("\nΔεν υπάρχουν νέα σήμερα.")
    out.append("\nΕνημερωτικό δελτίο. Επιβεβαιώστε πάντα στην επίσημη πηγή.")
    return "\n".join(out)


def render_json(deadlines, news):
    keep = ("id", "source", "title", "url", "published", "tags", "deadline", "deadline_evidence", "year_inferred")
    return json.dumps({"deadlines": [{k: r[k] for k in keep} for r in deadlines],
                       "news": [{k: r[k] for k in keep} for r in news]}, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------- delivery


def chunk(text, limit=3900):
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + block
    return parts + [cur] if cur else parts


def send_telegram(text, token, chat_id):
    for part in chunk(text):
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError(f"Telegram returned {r.status}")


def _same(a, b):
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


class _Api(BaseHTTPRequestHandler):
    admin_key = None  # set by `serve`; review endpoints are always locked behind it
    MAX_BODY = 64 * 1024

    def log_message(self, fmt, *args):  # keep request logs, but never print headers/keys
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _is_admin(self):
        return bool(self.admin_key) and _same(self.headers.get("X-Admin-Key"), self.admin_key)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        key = os.environ.get("DIGEST_API_KEY")
        if url.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        # Static interface (web/). It contains no data, so it is served without any key.
        name = "index.html" if url.path == "/" else url.path.lstrip("/")
        f = (WEB / name).resolve()
        if f.suffix in WEB_MIME and f.is_file() and WEB.resolve() in f.parents:
            return self._send(200, f.read_bytes(), WEB_MIME[f.suffix])
        if url.path == "/api/pending":
            if not self._is_admin():
                return self._send(401, b"unauthorized", "text/plain")
            conn = db()
            try:
                return self._json(200, {"items": pending_rows(conn)})
            finally:
                conn.close()
        if url.path == "/digest.json":
            if key and not _same(self.headers.get("X-API-Key"), key):
                return self._send(401, b"unauthorized", "text/plain")
            try:
                days = max(1, min(60, int(urllib.parse.parse_qs(url.query).get("days", ["2"])[0])))
            except ValueError:
                days = 2
            conn = db()
            try:
                body = render_json(*build(conn, days=days)).encode()
            finally:
                conn.close()
            return self._send(200, body, "application/json; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._is_admin():
            return self._send(401, b"unauthorized", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > self.MAX_BODY:
                return self._send(413, b"too large", "text/plain")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "invalid JSON body"})
        conn = db()
        try:
            if path == "/api/review":
                status = {"approve": "approved", "reject": "rejected", "undo": "pending"}.get(payload.get("action"))
                ids = payload.get("ids")
                if (not status or not isinstance(ids, list) or len(ids) > 500
                        or not all(isinstance(i, str) for i in ids)):
                    return self._json(400, {"error": "need action (approve|reject|undo) and ids (list of strings)"})
                return self._json(200, {"updated": set_status(conn, ids, status)})
            if path == "/api/deadline":
                item_id, dl = payload.get("id"), payload.get("deadline")
                if not isinstance(item_id, str) or not (dl is None or isinstance(dl, str)):
                    return self._json(400, {"error": "need id (string) and deadline (YYYY-MM-DD or null)"})
                try:
                    return self._json(200, {"updated": set_deadline(conn, item_id, dl)})
                except ValueError:
                    return self._json(400, {"error": "deadline must be YYYY-MM-DD"})
            self._send(404, b"not found", "text/plain")
        finally:
            conn.close()

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# ------------------------------------------------------------------------ CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch", help="pull sources into the review queue")
    p.add_argument("--auto-approve", action="store_true", help="skip human review (only once you trust the output)")
    sub.add_parser("check-sources", help="test every source and show how many items it returns")
    sub.add_parser("review", help="list items waiting for approval")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="*", help="item id prefixes")
        if name == "approve":
            p.add_argument("--all", action="store_true")
    p = sub.add_parser("digest", help="print the digest")
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--only-new", action="store_true")
    p = sub.add_parser("send-telegram", help="send the digest (needs TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    p.add_argument("--days", type=int, default=2)
    p = sub.add_parser("serve", help="read-only JSON API for a webapp")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = ap.parse_args(argv)

    conn = db()
    if a.cmd == "fetch":
        for r in ingest(conn, load_sources(), auto_approve=a.auto_approve):
            print(f"{r['source']}: found {r['found']}, new {r['new']}" + (f"  ERROR {r['error']}" if r["error"] else ""))
    elif a.cmd == "check-sources":
        for src in load_sources():
            try:
                items = collect(src)
                kept = sum(1 for i in items if classify(i["title"] + " " + i["description"]))
                print(f"OK    {src['name']}: {len(items)} items, {kept} match topics")
                for i in items[:3]:
                    print(f"        - {i['title'][:90]}")
            except Exception as e:
                print(f"FAIL  {src['name']}: {type(e).__name__}: {e}")
    elif a.cmd == "review":
        for r in conn.execute("SELECT * FROM items WHERE status='pending' ORDER BY published DESC"):
            print(f"[{r['id']}] {r['title']}\n    {r['source']} · {r['url']}")
            if r["deadline"]:
                print(f"    Προθεσμία {r['deadline']}: «{r['deadline_evidence']}»")
    elif a.cmd in ("approve", "reject"):
        status = "approved" if a.cmd == "approve" else "rejected"
        if a.cmd == "approve" and a.all:
            n = conn.execute("UPDATE items SET status='approved' WHERE status='pending'").rowcount
        else:
            n = sum(conn.execute("UPDATE items SET status=? WHERE id LIKE ? AND status='pending'",
                                 (status, i + "%")).rowcount for i in a.ids)
        conn.commit()
        print(f"{status}: {n}")
    elif a.cmd == "digest":
        d, n = build(conn, days=a.days, only_new=a.only_new)
        print(render_json(d, n) if a.format == "json" else render_text(d, n))
    elif a.cmd == "send-telegram":
        token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        d, n = build(conn, days=a.days, only_new=True)
        if not d and not n:
            return print("Nothing new to send.")
        send_telegram(render_text(d, n), token, chat)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany("UPDATE items SET sent_at=? WHERE id=?", [(now, r["id"]) for r in d + n])
        conn.commit()
        print(f"Sent {len(d)} deadlines and {len(n)} news items.")
    elif a.cmd == "serve":
        admin = os.environ.get("DIGEST_ADMIN_KEY")
        if not admin:
            admin = secrets.token_urlsafe(16)
            print(f"Admin key for the review page (generated for this run): {admin}")
            print("Set DIGEST_ADMIN_KEY to keep a fixed key.")
        _Api.admin_key = admin
        print(f"Serving on http://{a.host}:{a.port}/  (review page: /review.html)")
        ThreadingHTTPServer((a.host, a.port), _Api).serve_forever()


if __name__ == "__main__":
    main()