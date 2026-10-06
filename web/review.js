(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const KEY = "digest_admin_key";
  const state = { items: [], selected: new Set() };

  const store = {
    get() { try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; } },
    set(v) { try { sessionStorage.setItem(KEY, v); } catch { /* storage unavailable */ } },
  };

  // Everything from the API is untrusted: DOM with textContent only, never innerHTML.
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
  const fmt = (iso) => (iso ? parseDay(iso).toLocaleDateString("el-GR", { day: "2-digit", month: "2-digit", year: "numeric" }) : "");

  const setStatus = (msg, isError = false) => {
    const s = $("status");
    s.replaceChildren(document.createTextNode(msg));
    s.classList.toggle("error", isError);
    return s;
  };

  // ---- API -----------------------------------------------------------------
  class AuthError extends Error {}

  const api = async (path, body) => {
    const headers = { "X-Admin-Key": store.get() };
    const opts = { headers, cache: "no-store" };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers = { ...headers, "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) { askKey(); throw new AuthError("Απαιτείται κλειδί διαχειριστή."); }
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch { /* not JSON */ }
      throw new Error(msg);
    }
    return res.json();
  };

  const askKey = () => {
    const dlg = $("keydlg");
    if (typeof dlg.showModal === "function" && !dlg.open) dlg.showModal();
  };

  // ---- rendering -----------------------------------------------------------
  const card = (r) => {
    const li = el("li", "card review");
    li.dataset.id = r.id;

    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = state.selected.has(r.id);
    cb.setAttribute("aria-label", "Επιλογή: " + r.title);
    cb.addEventListener("change", () => {
      cb.checked ? state.selected.add(r.id) : state.selected.delete(r.id);
      syncAll();
    });

    const body = el("div");
    const h = el("h3");
    const a = el("a", null, r.title);
    a.href = safeUrl(r.url); a.target = "_blank"; a.rel = "noopener noreferrer";
    h.append(a);
    body.append(h, el("p", "meta", r.source + (r.published ? " · " + fmt(r.published) : "")));
    if (r.excerpt) body.append(el("p", "excerpt", r.excerpt));
    if (r.body) {  // the stored article text, so you can check the deadline before approving
      const d = el("details", "preview");
      d.append(el("summary", null, "Κείμενο άρθρου (αποθηκευμένο)"), el("div", "bodytext", r.body));
      body.append(d);
    }

    // Deadline: editable, because extraction can be wrong.
    const row = el("div", "deadline-row");
    const label = el("label", null, "Προθεσμία ");
    const input = el("input");
    input.type = "date";
    input.value = r.deadline || "";
    label.append(input);
    const clear = el("button", "ghost small", "Καθαρισμός");
    clear.type = "button";
    const note = el("span", "saved");
    const save = async (value) => {
      try {
        await api("/api/deadline", { id: r.id, deadline: value || null });
        r.deadline = value || null;
        if (!value) r.deadline_evidence = null;
        r.year_inferred = 0;
        note.textContent = "Αποθηκεύτηκε ✓";
        warn.hidden = true;
        if (!value) quote.hidden = true;
      } catch (e) {
        input.value = r.deadline || "";
        note.textContent = "Σφάλμα: " + e.message;
      }
    };
    input.addEventListener("change", () => save(input.value));
    clear.addEventListener("click", () => { input.value = ""; save(""); });
    row.append(label, clear, note);
    body.append(row);

    const quote = el("blockquote", null, r.deadline_evidence ? `«${r.deadline_evidence}»` : "");
    quote.hidden = !r.deadline_evidence;
    const warn = el("p", "warn", "Το έτος υποτέθηκε από την ημερομηνία δημοσίευσης. Ελέγξτε την πηγή.");
    warn.hidden = !r.year_inferred;
    body.append(quote, warn);

    if (r.tags) {
      const pills = el("div", "pills");
      r.tags.split(",").map((t) => t.trim()).filter(Boolean).forEach((t) => pills.append(el("span", "pill", t)));
      body.append(pills);
    }

    const actions = el("div", "row-actions");
    const ok = el("button", null, "Έγκριση");
    const no = el("button", "danger", "Απόρριψη");
    ok.type = no.type = "button";
    ok.addEventListener("click", () => act([r.id], "approve"));
    no.addEventListener("click", () => act([r.id], "reject"));
    actions.append(ok, no);

    li.append(cb, body, actions);
    return li;
  };

  const syncAll = () => {
    const all = $("all");
    all.checked = state.items.length > 0 && state.selected.size === state.items.length;
    all.indeterminate = state.selected.size > 0 && state.selected.size < state.items.length;
    $("approve-sel").disabled = $("reject-sel").disabled = state.selected.size === 0;
    $("approve-all").disabled = state.items.length === 0;
  };

  const render = () => {
    const ul = $("items");
    ul.replaceChildren();
    if (!state.items.length) ul.append(el("li", "empty", "Δεν υπάρχουν ειδήσεις σε αναμονή. 🎉"));
    state.items.forEach((r) => ul.append(card(r)));
    $("subtitle").textContent = `${state.items.length} σε αναμονή`;
    syncAll();
  };

  // ---- actions -------------------------------------------------------------
  const act = async (ids, action) => {
    if (!ids.length) return;
    try {
      const { updated } = await api("/api/review", { ids, action });
      const gone = new Set(ids);
      state.items = state.items.filter((r) => !gone.has(r.id));
      ids.forEach((i) => state.selected.delete(i));
      render();
      const verb = action === "approve" ? "εγκρίθηκαν" : "απορρίφθηκαν";
      const s = setStatus(`${updated} ${verb}. `);
      const undo = el("button", "ghost small", "Αναίρεση");
      undo.type = "button";
      undo.addEventListener("click", async () => {
        try {
          await api("/api/review", { ids, action: "undo" });
          await load();
          setStatus("Η ενέργεια αναιρέθηκε.");
        } catch (e) { setStatus(e.message, true); }
      });
      s.append(undo);
    } catch (e) {
      setStatus(e.message, true);
    }
  };

  const load = async () => {
    setStatus("Φόρτωση…");
    try {
      const { items } = await api("/api/pending");
      state.items = Array.isArray(items) ? items : [];
      state.selected = new Set([...state.selected].filter((id) => state.items.some((r) => r.id === id)));
      setStatus("");
      render();
    } catch (e) {
      if (!(e instanceof AuthError)) setStatus("Δεν ήταν δυνατή η φόρτωση. Ελέγξτε ότι ο διακομιστής τρέχει. (" + e.message + ")", true);
      else setStatus(e.message, true);
    }
  };

  // ---- wiring --------------------------------------------------------------
  $("reload").addEventListener("click", load);
  $("all").addEventListener("change", (e) => {
    state.selected = new Set(e.target.checked ? state.items.map((r) => r.id) : []);
    render();
  });
  $("approve-sel").addEventListener("click", () => act([...state.selected], "approve"));
  $("reject-sel").addEventListener("click", () => act([...state.selected], "reject"));
  $("approve-all").addEventListener("click", () => {
    if (confirm(`Έγκριση και των ${state.items.length} ειδήσεων χωρίς έλεγχο;`)) act(state.items.map((r) => r.id), "approve");
  });
  $("keydlg").addEventListener("close", () => {
    if ($("keydlg").returnValue === "ok" && $("keyinput").value) {
      store.set($("keyinput").value);
      $("keyinput").value = "";
      load();
    }
  });
  load();
})();
