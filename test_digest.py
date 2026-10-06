import tempfile
import unittest
from datetime import date
from pathlib import Path

import digest

TODAY = date(2026, 10, 3)

FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Παράταση προθεσμίας υποβολής δηλώσεων ΦΠΑ</title><link>http://x/1</link>
<description>&lt;p&gt;Παρατείνεται έως τις 31 Οκτωβρίου 2026 η υποβολή.&lt;/p&gt;</description>
<pubDate>Thu, 01 Oct 2026 08:00:00 +0300</pubDate></item>
<item><title>Παράταση προθεσμίας υποβολής δηλώσεων ΦΠΑ</title><link>http://x/1-dup</link>
<description>duplicate title</description><pubDate>Thu, 01 Oct 2026 09:00:00 +0300</pubDate></item>
<item><title>Αποτελέσματα ποδοσφαίρου</title><link>http://x/2</link>
<description>Κανένα σχετικό θέμα</description><pubDate>Thu, 01 Oct 2026 08:00:00 +0300</pubDate></item>
<item><title>Νέες οδηγίες μισθοδοσίας από τον ΕΦΚΑ</title><link>http://x/3</link>
<description>Γενικές οδηγίες.</description><pubDate>Thu, 01 Oct 2026 10:00:00 +0300</pubDate></item>
<item><title>Παλιά ανακοίνωση ΑΑΔΕ</title><link>http://x/4</link>
<description>Παλιό</description><pubDate>Sat, 01 Aug 2026 10:00:00 +0300</pubDate></item>
</channel></rss>"""


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        feed = Path(self.tmp.name) / "feed.xml"
        feed.write_text(FEED, encoding="utf-8")
        self.sources = [{"name": "Test", "type": "rss", "url": feed.as_uri(), "store_excerpt": False}]
        self.conn = digest.db(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_ingest_filters_dedupes_and_extracts_deadline(self):
        r = digest.ingest(self.conn, self.sources, today=TODAY)[0]
        self.assertIsNone(r["error"])
        self.assertEqual(r["new"], 2)  # VAT + payroll; duplicate, football and old item skipped
        self.assertEqual(digest.ingest(self.conn, self.sources, today=TODAY)[0]["new"], 0)
        row = self.conn.execute("SELECT * FROM items WHERE url='http://x/1'").fetchone()
        self.assertEqual(row["deadline"], "2026-10-31")
        self.assertIn("31 Οκτωβρίου 2026", row["deadline_evidence"])
        self.assertEqual(row["excerpt"], "")  # store_excerpt false

    def test_review_gate(self):
        digest.ingest(self.conn, self.sources, today=TODAY)
        self.assertEqual(digest.build(self.conn, today=TODAY), ([], []))
        self.conn.execute("UPDATE items SET status='approved'")
        d, n = digest.build(self.conn, today=TODAY)
        # VAT states a deadline -> in deadlines AND in articles; payroll has none -> articles only
        self.assertEqual((len(d), len(n)), (1, 2))
        text = digest.render_text(d, n, TODAY)
        self.assertIn("31/10/2026", text)
        self.assertIn("σε 28 ημέρες", text)
        self.assertEqual(text.count("Παράταση προθεσμίας υποβολής δηλώσεων ΦΠΑ"), 1)  # not listed twice
        self.assertIn("ΑΡΘΡΑ", text)

    def test_deadline_rules(self):
        f = digest.find_deadline
        self.assertEqual(f("Υποβολή μέχρι 15/10/2026", TODAY)["date"], "2026-10-15")
        self.assertIsNone(f("Η σύσκεψη έγινε 15/10/2026", TODAY))  # no deadline cue
        y = f("Προθεσμία έως 31 Δεκεμβρίου", TODAY)
        self.assertEqual((y["date"], y["year_inferred"]), ("2026-12-31", True))
        self.assertEqual(f("έως 5 Ιανουαρίου", TODAY)["date"], "2027-01-05")  # rolls to next year
        self.assertIsNone(f("έως 31/02/2026", TODAY))  # invalid date

    def test_classify(self):
        self.assertIn("ΦΠΑ", digest.classify("Νέα ΦΠΑ"))
        self.assertEqual(digest.classify("Αποτελέσματα ποδοσφαίρου"), [])


CAL_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>cal</title>
<item><title>Υποβολή δήλωσης ΦΜΥ</title><link>http://x/e/1</link><pubDate>Mon, 05 Oct 2026 00:00:00 +0300</pubDate></item>
<item><title>Υποβολή δήλωσης ΦΜΥ</title><link>http://x/e/2</link><pubDate>Sat, 31 Oct 2026 00:00:00 +0300</pubDate></item>
<item><title>Παλιά προθεσμία</title><link>http://x/e/3</link><pubDate>Mon, 28 Sep 2026 00:00:00 +0300</pubDate></item>
<item><title>Μακρινή προθεσμία</title><link>http://x/e/4</link><pubDate>Mon, 30 Nov 2026 00:00:00 +0300</pubDate></item>
</channel></rss>"""

LISTING = """<html><body>
<a href="/deltia-typoy-anakoinoseis/deltio-typoy-02102026">Νέες προθεσμίες για την ηλεκτρονική τιμολόγηση των επιχειρήσεων</a>
<a href="/deltia-typoy-anakoinoseis/deltio-typoy-01082026">Παλιό δελτίο για ΦΠΑ και προθεσμίες υποβολής</a>
<a href="/about">Σχετικά με εμάς και την υπηρεσία μας</a>
<a href="/deltia-typoy-anakoinoseis/deltio-typoy-02102026-0">Σύντομο</a>
</body></html>"""


class SourceTypeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = digest.db(str(Path(self.tmp.name) / "s.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def file_url(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text, encoding="utf-8")
        return p.as_uri()

    def test_calendar_feed_pubdate_is_deadline(self):
        src = {"name": "Ημερολόγιο", "type": "rss", "url": self.file_url("c.xml", CAL_FEED),
               "pubdate_is_deadline": True, "max_ahead_days": 30, "keep_all": True, "store_excerpt": False}
        r = digest.ingest(self.conn, [src], today=TODAY)[0]
        self.assertEqual(r["new"], 2)  # past and too-far events skipped; recurring title kept
        rows = self.conn.execute("SELECT * FROM items ORDER BY deadline").fetchall()
        self.assertEqual([x["deadline"] for x in rows], ["2026-10-05", "2026-10-31"])
        self.assertIsNone(rows[0]["published"])
        self.assertIn("05/10/2026", rows[0]["deadline_evidence"])
        self.assertEqual(digest.ingest(self.conn, [src], today=TODAY)[0]["new"], 0)

    def test_calendar_events_are_deadlines_only_never_articles(self):
        name = "Taxheaven – Φορολογικό ημερολόγιο"  # configured with pubdate_is_deadline in sources.json
        self.assertIn(name, digest.event_sources())
        src = {"name": name, "type": "rss", "url": self.file_url("c.xml", CAL_FEED),
               "pubdate_is_deadline": True, "max_ahead_days": 30, "keep_all": True, "store_excerpt": False}
        digest.ingest(self.conn, [src], today=TODAY)
        self.conn.execute("UPDATE items SET status='approved'")
        d, n = digest.build(self.conn, today=TODAY, days=30)
        self.assertEqual(len(d), 2)
        self.assertEqual(n, [])  # events never appear in the articles list
        self.assertTrue(all(r["kind"] == "event" for r in d))
        import json
        out = json.loads(digest.render_json(d, n))
        self.assertEqual({r["kind"] for r in out["deadlines"]}, {"event"})

    def test_articles_respect_the_day_window(self):
        from datetime import timedelta
        art = {"name": "Άρθρα", "type": "rss", "url": self.file_url("a.xml", FEED)}
        digest.ingest(self.conn, [art], today=TODAY)
        self.conn.execute("UPDATE items SET status='approved'")
        d, n = digest.build(self.conn, today=TODAY + timedelta(days=10), days=2)
        self.assertEqual(len(n), 0)   # published Oct 1, window is Oct 11-13: too old for articles
        self.assertEqual(len(d), 1)   # but its deadline (Oct 31) is still upcoming
        self.assertEqual(d[0]["kind"], "article")

    def test_html_listing_with_url_dates(self):
        src = {"name": "ΑΑΔΕ", "type": "html_links", "url": self.file_url("l.html", LISTING),
               "link_pattern": "/deltia-typoy-anakoinoseis/deltio-typoy-",
               "url_date_pattern": r"deltio-typoy-(\d{2})(\d{2})(\d{4})", "max_items": 10}
        r = digest.ingest(self.conn, [src], today=TODAY)[0]
        self.assertEqual(r["found"], 2)  # /about and the too-short anchor text are ignored
        self.assertEqual(r["new"], 1)    # the August release is older than max_age_days
        self.assertEqual(self.conn.execute("SELECT published FROM items").fetchone()[0], "2026-10-02")

    def test_source_status_is_recorded_and_exposed(self):
        import json
        good = {"name": "Καλή", "type": "rss", "url": self.file_url("g.xml", FEED)}
        bad = {"name": "Χαλασμένη", "type": "rss", "url": (Path(self.tmp.name) / "missing.xml").as_uri()}
        cfg = Path(self.tmp.name) / "src.json"
        cfg.write_text(json.dumps({"sources": [good, bad]}), encoding="utf-8")
        old = digest.SOURCES_PATH
        digest.SOURCES_PATH = str(cfg)
        try:
            digest.ingest(self.conn, [good, bad], today=TODAY)
            st = {s["source"]: s for s in digest.source_status(self.conn)}
            self.assertIsNone(st["Καλή"]["error"])
            self.assertEqual((st["Καλή"]["found"], st["Καλή"]["matched"], st["Καλή"]["new"]), (5, 3, 2))  # 3 topic matches incl. the duplicate title, 2 stored
            self.assertIn("URLError", st["Χαλασμένη"]["error"])  # the failure is visible, not only in a log
            data = json.loads(digest.render_json([], [], sources=list(st.values())))
            self.assertEqual([s["source"] for s in data["sources"]], ["Καλή", "Χαλασμένη"])
            # a source that has never been fetched is still listed
            cfg.write_text(json.dumps({"sources": [good, bad, {"name": "Νέα", "type": "rss", "url": "x"}]}))
            names = {s["source"]: s for s in digest.source_status(self.conn)}
            self.assertIsNone(names["Νέα"]["at"])
        finally:
            digest.SOURCES_PATH = old

    def test_shipped_sources_json_is_wellformed(self):
        import re
        sources = digest.load_sources(str(Path(__file__).parent / "sources.json"))
        self.assertGreaterEqual(len(sources), 3)
        for s in sources:
            self.assertIn(s["type"], ("rss", "html_links"))
            self.assertTrue(s["url"].startswith("https://"))
            for k in ("link_pattern", "url_date_pattern"):
                if k in s:
                    re.compile(s[k])
        aade = next(s for s in sources if s["name"].startswith("ΑΑΔΕ"))
        link = "https://www.aade.gr/deltia-typoy-anakoinoseis/deltio-typoy-02102026-1"
        self.assertTrue(re.search(aade["link_pattern"], link))
        self.assertEqual(re.search(aade["url_date_pattern"], link).groups(), ("02", "10", "2026"))
        efka = next(s for s in sources if s["name"].startswith("e-ΕΦΚΑ"))
        self.assertTrue(re.search(efka["link_pattern"], "https://www.e-efka.gov.gr/el/anakoinoseis/anakoinosi-387"))


class PublicConfigTests(unittest.TestCase):
    def test_public_host_needs_strong_admin_key(self):
        c = digest.check_public_config
        self.assertIsNone(c("127.0.0.1", None))          # local use: key may be generated
        self.assertIsNone(c("localhost", ""))
        self.assertIn("DIGEST_ADMIN_KEY", c("0.0.0.0", None))
        self.assertIn("DIGEST_ADMIN_KEY", c("0.0.0.0", "short"))
        self.assertIsNone(c("0.0.0.0", "x" * 16))

    def test_serve_refuses_to_start_unsafe(self):
        import os
        old = os.environ.pop("DIGEST_ADMIN_KEY", None)
        try:
            with self.assertRaises(SystemExit) as cm:
                digest.main(["serve", "--host", "0.0.0.0", "--port", "0"])
            self.assertIn("admin key", str(cm.exception.code))
        finally:
            if old is not None:
                os.environ["DIGEST_ADMIN_KEY"] = old


class ReviewApiTests(unittest.TestCase):
    """Starts the real HTTP server on a free port with a temp database."""

    def setUp(self):
        import json
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        self.json, self.urlreq = json, urllib.request
        self.tmp = tempfile.TemporaryDirectory()
        digest.DB_PATH = str(Path(self.tmp.name) / "api.db")
        conn = digest.db()
        feed = Path(self.tmp.name) / "feed.xml"
        feed.write_text(FEED, encoding="utf-8")
        digest.ingest(conn, [{"name": "T", "type": "rss", "url": feed.as_uri()}], today=TODAY)
        conn.close()
        digest._Api.admin_key = "adm"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), digest._Api)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, key="adm"):
        headers = {"X-Admin-Key": key} if key is not None else {}
        data = None
        if body is not None:
            data = self.json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = self.urlreq.Request(self.base + path, data=data, headers=headers)
        try:
            with self.urlreq.urlopen(req) as r:
                return r.status, self.json.loads(r.read())
        except self.urlreq.HTTPError as e:
            raw = e.read()
            try:
                return e.code, self.json.loads(raw)
            except ValueError:
                return e.code, raw

    def test_admin_key_required(self):
        self.assertEqual(self.call("/api/pending", key=None)[0], 401)
        self.assertEqual(self.call("/api/pending", key="wrong")[0], 401)
        self.assertEqual(self.call("/api/review", {"ids": [], "action": "approve"}, key="wrong")[0], 401)
        self.assertEqual(self.call("/api/pending")[0], 200)

    def test_review_flow(self):
        items = self.call("/api/pending")[1]["items"]
        self.assertEqual(len(items), 2)
        vat = next(i for i in items if i["deadline"])
        other = next(i for i in items if not i["deadline"])
        self.assertEqual(self.call("/api/review", {"ids": [vat["id"]], "action": "approve"})[1]["updated"], 1)
        self.assertEqual(self.call("/api/review", {"ids": [other["id"]], "action": "reject"})[1]["updated"], 1)
        self.assertEqual(self.call("/api/pending")[1]["items"], [])
        conn = digest.db()
        d, n = digest.build(conn, today=TODAY)
        conn.close()
        self.assertEqual((len(d), len(n)), (1, 1))  # the rejected payroll item never reaches the digest
        self.call("/api/review", {"ids": [other["id"]], "action": "undo"})
        self.assertEqual(len(self.call("/api/pending")[1]["items"]), 1)

    def test_deadline_edit_and_validation(self):
        vat = next(i for i in self.call("/api/pending")[1]["items"] if i["deadline"])
        self.assertEqual(self.call("/api/deadline", {"id": vat["id"], "deadline": "2026-11-05"})[0], 200)
        row = next(i for i in self.call("/api/pending")[1]["items"] if i["id"] == vat["id"])
        self.assertEqual((row["deadline"], row["year_inferred"]), ("2026-11-05", 0))
        self.assertEqual(self.call("/api/deadline", {"id": vat["id"], "deadline": "31/10/2026"})[0], 400)
        self.assertEqual(self.call("/api/deadline", {"id": vat["id"], "deadline": None})[0], 200)
        self.assertEqual(self.call("/api/review", {"ids": "x", "action": "approve"})[0], 400)
        self.assertEqual(self.call("/api/review", {"ids": [], "action": "delete"})[0], 400)


if __name__ == "__main__":
    unittest.main()
