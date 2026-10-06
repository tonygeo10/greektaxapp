(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const KEY = "digest_api_key";
  const state = { deadlines: [], news: [], tag: "", q: "", updated: null };

  // ---- helpers -------------------------------------------------------------
  const store = {
    get() { try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; } },
    set(v) { try { sessionStorage.setItem(KEY, v); } catch { /* storage unavailable */ } },
  };

  const fold = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/ς/g, "σ");

  // Everything from the API is untrusted: build DOM with textContent, never innerHTML.
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const safeUrl = (u) => {
    try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : "#"; } catch { return "#"; }
  };

  const parseDay = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const today = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
  const daysLeft = (iso) => Math.round((parseDay(iso) - today()) / 864e5);
  const fmt = (d) => d.toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit", year: "numeric" });
  const whenText = (n) => (n <= 0 ? "σήμερα" : n === 1 ? "αύριο" : `σε ${n} ημέρες`);
  const tagsOf = (r) => (r.tags || "").split(",").map((t) => t.trim()).filter(Boolean);

  const setStatus = (msg, isError = false) => {
    const s = $("status");
    s.textContent = msg;
    s.classList.toggle("error", isError);
  };

  // ---- filtering -----------------------------------------------------------
  const matches = (r) => {
    if (state.tag && !tagsOf(r).includes(state.tag)) return false;
    if (!state.q) return true;
    return fold([r.title, r.source, r.tags].join(" ")).includes(fold(state.q));
  };

  // ---- rendering -----------------------------------------------------------
  const pills = (r) => {
    const tags = tagsOf(r);
    if (!tags.length) return null;
    const box = el("div", "pills");
    tags.forEach((t) => box.append(el("span", "pill", t)));
    return box;
  };

  const titleLink = (r) => {
    const h = el("h3");
    const a = el("a", null, r.title);
    a.href = safeUrl(r.url);
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    h.append(a);
    return h;
  };

  const deadlineCard = (r) => {
    const n = daysLeft(r.deadline);
    const date = parseDay(r.deadline);
    const li = el("li", "card deadline " + (n <= 3 ? "urgent" : n <= 10 ? "soon" : "later"));

    const badge = el("div", "datebadge");
    badge.append(el("span", "day", String(date.getDate())),
                 el("span", "mon", date.toLocaleDateString("el-GR", { month: "short" })));

    const body = el("div");
    const meta = el("p", "meta");
    meta.append(el("strong", "when", whenText(n)), document.createTextNode(` · ${fmt(date)} · ${r.source}`));
    body.append(titleLink(r), meta);
    if (r.deadline_evidence) body.append(el("blockquote", null, `«${r.deadline_evidence}»`));
    if (r.year_inferred) body.append(el("p", "warn", "Το έτος υποτέθηκε από την ημερομηνία δημοσίευσης. Ελέγξτε την πηγή."));
    const p = pills(r);
    if (p) body.append(p);

    li.append(badge, body);
    return li;
  };

  const newsCard = (r) => {
    const li = el("li", "card");
    const meta = el("p", "meta");
    const published = r.published ? ` · ${fmt(parseDay(r.published))}` : "";
    meta.textContent = r.source + published;
    li.append(titleLink(r), meta);
    const p = pills(r);
    if (p) li.append(p);
    return li;
  };

  const renderChips = () => {
    const counts = new Map();
    [...state.deadlines, ...state.news].forEach((r) => tagsOf(r).forEach((t) => counts.set(t, (counts.get(t) || 0) + 1)));
    const box = $("tags");
    box.replaceChildren();
    const make = (label, value) => {
      const b = el("button", "chip", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(state.tag === value));
      b.addEventListener("click", () => { state.tag = value; render(); });
      return b;
    };
    if (counts.size) box.append(make("Όλα", ""));
    [...counts.entries()].sort((a, b) => b[1] - a[1]).forEach(([t, c]) => box.append(make(`${t} (${c})`, t)));
  };

  const fillList = (id, rows, build, emptyText) => {
    const ul = $(id);
    ul.replaceChildren();
    if (!rows.length) {
      const li = el("li", "empty", emptyText);
      ul.append(li);
      return;
    }
    rows.forEach((r) => ul.append(build(r)));
  };

  const filtered = () => ({
    deadlines: state.deadlines.filter((r) => r.deadline && daysLeft(r.deadline) >= 0).filter(matches),
    news: state.news.filter(matches),
  });

  const render = () => {
    renderChips();
    const { deadlines, news } = filtered();
    fillList("deadlines", deadlines, deadlineCard, "Δεν υπάρχουν ενεργές προθεσμίες.");
    fillList("news", news, newsCard, "Δεν υπάρχουν νέα για αυτή την περίοδο.");
    const t = state.updated ? state.updated.toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit" }) : "";
    $("subtitle").textContent = state.updated
      ? `Ενημερώθηκε ${t} · ${deadlines.length} προθεσμίες · ${news.length} νέα`
      : "";
  };

  // ---- copy as plain text (for WhatsApp / email) ---------------------------
  const toText = () => {
    const { deadlines, news } = filtered();
    const out = [`📋 Λογιστικό Δελτίο — ${fmt(today())}`];
    if (deadlines.length) {
      out.push("", "⏰ ΠΡΟΘΕΣΜΙΕΣ");
      deadlines.forEach((r) => {
        const n = daysLeft(r.deadline);
        const icon = n <= 3 ? "🔴" : n <= 10 ? "🟠" : "🟡";
        out.push("", `${icon} ${fmt(parseDay(r.deadline))} (${whenText(n)}) — ${r.title}`, `   Πηγή: ${r.source} · ${r.url}`);
        if (r.deadline_evidence) out.push(`   Από το κείμενο: «${r.deadline_evidence}»`);
      });
    }
    if (news.length) {
      out.push("", "📰 ΝΕΑ");
      news.forEach((r) => out.push("", `• ${r.title}`, `   ${r.source} · ${r.url}`));
    }
    if (!deadlines.length && !news.length) out.push("", "Δεν υπάρχουν νέα σήμερα.");
    out.push("", "Ενημερωτικό δελτίο. Επιβεβαιώστε πάντα στην επίσημη πηγή.");
    return out.join("\n");
  };

  const copyText = async () => {
    const text = toText();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = el("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.append(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const b = $("copy");
    const old = b.textContent;
    b.textContent = "Αντιγράφηκε ✓";
    setTimeout(() => { b.textContent = old; }, 1500);
  };

  // ---- data ----------------------------------------------------------------
  const askKey = () => {
    const dlg = $("keydlg");
    if (typeof dlg.showModal === "function" && !dlg.open) dlg.showModal();
  };

  const load = async () => {
    const btn = $("refresh");
    btn.disabled = true;
    setStatus("Φόρτωση…");
    try {
      const headers = {};
      const key = store.get();
      if (key) headers["X-API-Key"] = key;
      const res = await fetch(`/digest.json?days=${encodeURIComponent($("days").value)}`, { headers, cache: "no-store" });
      if (res.status === 401) { setStatus("Απαιτείται κλειδί πρόσβασης.", true); askKey(); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.deadlines = Array.isArray(data.deadlines) ? data.deadlines : [];
      state.news = Array.isArray(data.news) ? data.news : [];
      state.updated = new Date();
      setStatus("");
      render();
    } catch (e) {
      setStatus(`Δεν ήταν δυνατή η φόρτωση του δελτίου. Ελέγξτε ότι ο διακομιστής τρέχει. (${e.message})`, true);
    } finally {
      btn.disabled = false;
    }
  };

  // ---- wiring --------------------------------------------------------------
  $("refresh").addEventListener("click", load);
  $("copy").addEventListener("click", copyText);
  $("days").addEventListener("change", load);
  $("q").addEventListener("input", (e) => { state.q = e.target.value.trim(); render(); });
  $("keydlg").addEventListener("close", () => {
    if ($("keydlg").returnValue === "ok" && $("keyinput").value) {
      store.set($("keyinput").value);
      $("keyinput").value = "";
      load();
    }
  });
  setInterval(load, 10 * 60 * 1000);
  load();
})();