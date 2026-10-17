/* VidyaSetu front end: plain JavaScript, no build step. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const state = {
    cfg: null, lid: null, lesson: null, rendered: 0, poll: null, quiz: null,
    sub: "read", view: "both", scale: 2,
    tts: { token: 0, cancel: null, audio: null }, warned: {},
  };
  const SCALES = [0.88, 1, 1.15, 1.3, 1.5];
  const RATES = [0.8, 1, 1.25, 1.5];
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- small helpers ---------- */
  function h(tag, props = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v; // only ever called with fixed strings from this file
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(kid));
    return e;
  }
  const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  const pref = (k, d) => { try { const v = JSON.parse(localStorage.getItem("vs." + k)); return v == null ? d : v; } catch { return d; } };
  const setPref = (k, v) => { try { localStorage.setItem("vs." + k, JSON.stringify(v)); } catch { /* private mode */ } };

  async function api(path, opts = {}) {
    let res;
    try { res = await fetch(path, opts); } catch { throw new Error("Cannot reach the VidyaSetu server. Is it running?"); }
    let data = null;
    try { data = await res.json(); } catch { /* not json */ }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }
  const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  let toastTimer;
  function toast(msg, ms = 4000) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), ms);
  }

  const langInfo = (code) => (state.cfg.languages.find((l) => l.code === code) || { name: code, native: code, speech: "en-IN" });
  const rtl = (lang) => (lang === "ur" ? "rtl" : null);
  const setBusy = (btn, busy, label) => {
    if (busy) { btn.dataset.html = btn.innerHTML; btn.replaceChildren(h("span", { class: "spin" }), label || "Working…"); }
    else if (btn.dataset.html) btn.innerHTML = btn.dataset.html;
    btn.disabled = busy;
  };
  function relTime(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 172800) return "yesterday";
    return new Date(ts * 1000).toLocaleDateString([], { day: "numeric", month: "short" });
  }

  /* ---------- speech ---------- */
  // Studio voice (Sarvam Bulbul, via our server) when configured; otherwise the browser's own voices.
  const stripLatin = (t) => t.replace(/\s*\([A-Za-z][A-Za-z\s\-]*\)/g, "");
  const audioCache = new Map(); // "lang|text" -> Promise<object URL>
  const studioOn = (lang) => !!lang && state.cfg.providers.speech === "sarvam" && langInfo(lang).neural_tts;

  function studioAudio(text, lang) {
    const key = `${lang}|${text}`;
    if (!audioCache.has(key)) {
      const p = fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, lang }) })
        .then(async (r) => {
          if (!r.ok) { let m = ""; try { m = (await r.json()).error; } catch { /* ignore */ } throw new Error(m || `voice error ${r.status}`); }
          return URL.createObjectURL(await r.blob());
        });
      p.catch(() => audioCache.delete(key));
      audioCache.set(key, p);
    }
    return audioCache.get(key);
  }
  function pickVoice(speech) {
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    const norm = (v) => v.lang.replace("_", "-").toLowerCase();
    return voices.find((v) => norm(v) === speech.toLowerCase()) || voices.find((v) => norm(v).startsWith(speech.slice(0, 2).toLowerCase()));
  }
  function cutAudio() {
    if (state.tts.cancel) { const c = state.tts.cancel; state.tts.cancel = null; c(); }
    if (window.speechSynthesis) speechSynthesis.cancel();
  }

  async function playStudio(text, lang, token, rate, onStart) {
    const url = await studioAudio(text, lang);
    if (token !== state.tts.token) return;
    await new Promise((resolve, reject) => {
      const a = new Audio(url);
      a.playbackRate = rate;
      let settled = false;
      const finish = (err) => { if (settled) return; settled = true; state.tts.cancel = null; state.tts.audio = null; err ? reject(err) : resolve(); };
      a.onended = () => finish();
      a.onerror = () => finish(new Error("audio could not be played"));
      a.onplaying = () => onStart && onStart();
      state.tts.audio = a;
      state.tts.cancel = () => { a.pause(); finish(); };
      a.play().catch((e) => finish(e));
    });
  }
  function speakBrowser(text, speech, local, token, rate, onStart) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) { toast("This browser cannot read text aloud."); return resolve(); }
      const voice = pickVoice(speech);
      if (!voice && !state.warned[speech]) {
        state.warned[speech] = true;
        toast(`No ${speech} voice found on this device. Install that language's text-to-speech voice in your system settings to hear it.`, 7000);
      }
      const chunks = (local ? stripLatin(text) : text).split(/(?<=[।.!?])\s+/).filter(Boolean);
      let i = 0;
      const next = () => {
        if (token !== state.tts.token || i >= chunks.length) return resolve();
        const u = new SpeechSynthesisUtterance(chunks[i]);
        u.lang = speech; if (voice) u.voice = voice; u.rate = 0.92 * P.rate;
        if (i++ === 0) u.onstart = () => onStart && onStart();
        u.onend = next; u.onerror = () => resolve();
        speechSynthesis.speak(u);
      };
      next();
    });
  }
  // Plays one piece of text and resolves when it ends (or is cut off).
  async function speak(text, { speech, local = true, lang = null, onStart } = {}) {
    const token = ++state.tts.token;
    cutAudio();
    if (local && studioOn(lang)) {
      try { await playStudio(text, lang, token, P.rate, onStart); return; }
      catch (err) {
        if (token !== state.tts.token) return;
        if (!state.warned.studio) { state.warned.studio = true; toast(`Studio voice unavailable (${err.message}). Using the browser voice instead.`, 6000); }
      }
    }
    if (token === state.tts.token) await speakBrowser(text, speech, local, token, P.rate, onStart);
  }

  /* ---------- the listening player ---------- */
  const P = { on: false, idx: 0, paused: false, once: false, run: 0, rate: 1 };
  const archLabel = (i) => {
    const l = state.lesson, eng = state.view === "english";
    return `Listen to paragraph ${i + 1} in ${eng || !l ? "English" : langInfo(l.lang).name}`;
  };
  const textFor = (p) => (state.view === "english" ? p.simple : p.translated);
  function findNext(from, dir) {
    const ps = state.lesson ? state.lesson.paragraphs : [];
    for (let i = from + dir; i >= 0 && i < ps.length; i += dir) if (textFor(ps[i])) return i;
    return -1;
  }
  function mark(idx, phase) {
    $$(".pair.playing").forEach((p) => {
      p.classList.remove("playing");
      const a = $(".arch", p); a.classList.remove("is-playing", "is-loading"); a.setAttribute("aria-label", archLabel(+p.dataset.i));
    });
    if (idx == null) return;
    const pair = $(`.pair[data-i="${idx}"]`); if (!pair) return;
    pair.classList.add("playing");
    const a = $(".arch", pair);
    a.classList.add(phase === "loading" ? "is-loading" : "is-playing");
    a.setAttribute("aria-label", "Stop listening");
  }
  function setListenLabel() {
    const b = $("#listenBtn");
    b.textContent = P.on ? "Stop listening" : "Listen to lesson";
  }
  function syncPlayer() {
    const l = state.lesson; if (!l || !P.on) return;
    const eng = state.view === "english";
    $("#plLabel").textContent = `Paragraph ${P.idx + 1}/${l.paragraphs.length}`;
    $("#plSub").textContent = `${eng ? "English" : langInfo(l.lang).name} · ${(textFor(l.paragraphs[P.idx]) || "").slice(0, 48)}…`;
    const t = $("#plToggle"); t.classList.toggle("is-playing", !P.paused);
    t.setAttribute("aria-label", P.paused ? "Play" : "Pause");
    $("#plPrev").disabled = findNext(P.idx, -1) < 0;
    $("#plNext").disabled = findNext(P.idx, 1) < 0;
    $("#plRate").textContent = `${P.rate}x`;
    $("#plRate").setAttribute("aria-label", `Speaking speed ${P.rate} times. Change`);
  }
  function silence() { // stop everything that makes sound
    P.on = false; P.paused = false; P.run++; state.tts.token++;
    cutAudio(); mark(null);
    $("#player").hidden = true; setListenLabel();
  }
  async function runPlayer(start, once) {
    silence();
    const run = ++P.run;
    P.on = true; P.paused = false; P.once = once; P.idx = start;
    $("#player").hidden = false; setListenLabel();
    while (P.on && run === P.run) {
      const l = state.lesson, p = l.paragraphs[P.idx], eng = state.view === "english";
      mark(P.idx, "loading"); syncPlayer();
      const row = $(`.pair[data-i="${P.idx}"]`);
      if (row) row.scrollIntoView({ block: "center", behavior: reduceMotion() ? "auto" : "smooth" });
      const nxt = l.paragraphs[findNext(P.idx, 1)];
      if (nxt && !eng && studioOn(l.lang)) studioAudio(nxt.translated, l.lang).catch(() => {}); // warm the next one
      await speak(textFor(p), {
        speech: eng ? "en-IN" : langInfo(l.lang).speech, local: !eng, lang: eng ? null : l.lang,
        onStart: () => { if (run === P.run) mark(P.idx, "playing"); },
      });
      if (run !== P.run) return; // paused, skipped or stopped
      if (P.once) break;
      const n = findNext(P.idx, 1);
      if (n < 0) break;
      P.idx = n;
    }
    if (run === P.run) silence();
  }
  function togglePause() {
    if (!P.on) return;
    if (P.paused) return runPlayer(P.idx, P.once);
    P.paused = true; P.run++; state.tts.token++; cutAudio();
    $("#player").hidden = false; syncPlayer();
  }
  function seek(dir) {
    const n = findNext(P.idx, dir);
    if (n >= 0) runPlayer(n, false);
  }
  $("#listenBtn").addEventListener("click", () => {
    if (P.on) return silence();
    const first = findNext(-1, 1);
    if (first >= 0) runPlayer(first, false); else toast("Nothing to listen to yet.");
  });
  $("#plToggle").addEventListener("click", togglePause);
  $("#plPrev").addEventListener("click", () => seek(-1));
  $("#plNext").addEventListener("click", () => seek(1));
  $("#plClose").addEventListener("click", silence);
  $("#plRate").addEventListener("click", () => {
    P.rate = RATES[(RATES.indexOf(P.rate) + 1) % RATES.length]; setPref("rate", P.rate);
    if (state.tts.audio) state.tts.audio.playbackRate = P.rate;
    syncPlayer();
  });
  // Speak one piece of text outside the player (answers, terms, the hero sentence).
  function sayOnce(text, lang, local = true) {
    silence();
    const info = lang ? langInfo(lang) : null;
    return speak(text, { speech: info ? info.speech : "en-IN", local, lang });
  }

  /* ---------- navigation ---------- */
  function parseHash() {
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    if (parts[0] === "teacher") return { view: "teacher" };
    if (parts[0] === "lesson" && /^[0-9a-f]{10}$/.test(parts[1] || "")) return { view: "lesson", id: parts[1] };
    if (/^[0-9a-f]{10}$/.test(parts[0] || "")) return { view: "lesson", id: parts[0] }; // old-style links
    return { view: "home" };
  }
  const go = (path) => { if (location.hash === "#" + path) route(); else location.hash = path; };

  let shown = null;
  function showScreen(name) {
    $("#view-learn").hidden = name === "teacher";
    $("#home").hidden = name !== "home";
    $("#lessonView").hidden = name !== "lesson";
    $("#view-teacher").hidden = name !== "teacher";
    $$(".nav-btn").forEach((b) => b.setAttribute("aria-current", (b.dataset.view === "teacher") === (name === "teacher") ? "page" : "false"));
    if (shown !== name) window.scrollTo(0, 0);
    shown = name;
    if (name !== "lesson") document.title = name === "teacher" ? "For teachers · VidyaSetu" : "VidyaSetu";
  }
  async function route() {
    const r = parseHash(), shownBefore = shown;
    if (r.view !== "lesson" || r.id !== state.lid) silence();
    if (r.view === "lesson") {
      if (r.id === state.lid && state.lesson) { showScreen("lesson"); if (shownBefore !== "lesson") setSub("read"); return; }
      showScreen("lesson");
      await openLesson(r.id);
    } else {
      showScreen(r.view);
      if (r.view === "home") loadHistory();
    }
  }
  window.addEventListener("hashchange", route);
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => go(b.dataset.view === "teacher" ? "/teacher" : "/")));
  $("#brandLink").addEventListener("click", (e) => { e.preventDefault(); go("/"); });
  $("#backBtn").addEventListener("click", () => go("/"));
  $("#newBtn").addEventListener("click", () => { go("/"); setTimeout(() => $("#makeH").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth" }), 50); });
  $("#printBtn").addEventListener("click", () => { silence(); setSub("read"); window.print(); });

  /* ---------- home: make a lesson ---------- */
  const segSrc = $$("#makeForm .mode");
  function setSource(src) {
    segSrc.forEach((x) => x.setAttribute("aria-selected", String(x.dataset.src === src)));
    $$("#makeForm .src-pane").forEach((p) => (p.hidden = p.dataset.pane !== src));
    setPref("src", src);
  }
  segSrc.forEach((b) => b.addEventListener("click", () => setSource(b.dataset.src)));
  const currentSource = () => segSrc.find((b) => b.getAttribute("aria-selected") === "true").dataset.src;

  const fileInput = $("#file"), drop = $("#drop");
  const dropDefault = [...$("#dropText").childNodes].map((n) => n.cloneNode(true));
  function showFile(f) {
    drop.classList.toggle("has-file", !!f);
    if (f) fill($("#dropText"), h("b", { text: f.name }), h("span", { text: `${Math.max(1, Math.round(f.size / 1024))} KB. Click to choose a different file.` }));
    else fill($("#dropText"), ...dropDefault.map((n) => n.cloneNode(true)));
  }
  fileInput.addEventListener("change", () => showFile(fileInput.files[0]));
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) { fileInput.files = e.dataTransfer.files; showFile(fileInput.files[0]); } });

  $("#text").addEventListener("input", (e) => {
    const n = e.target.value.trim().split(/\s+/).filter(Boolean).length;
    $("#textCount").textContent = n ? `${n} word${n === 1 ? "" : "s"} pasted.` : "Nothing pasted yet.";
  });
  ["Photosynthesis", "The water cycle", "Parts of a flower", "Newton's laws of motion", "Fractions and decimals", "The Indian monsoon"].forEach((t) =>
    $("#topicChips").append(h("button", { type: "button", class: "tchip", text: t, onclick: () => { $("#topic").value = t; $("#topic").focus(); } })));

  // Language and class pickers (the hidden inputs hold the value the form sends)
  function setLang(code) {
    $("#lang").value = code; setPref("lang", code);
    $$("#langPick .lang-tile").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.code === code)));
  }
  function setGrade(n) {
    $("#grade").value = n; setPref("grade", n);
    $$("#gradePick .grade-btn").forEach((b) => b.setAttribute("aria-checked", String(+b.dataset.n === n)));
  }
  function radioKeys(group) { // arrow keys move between the choices
    group.addEventListener("keydown", (e) => {
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) return;
      const items = [...group.children], i = items.indexOf(document.activeElement);
      if (i < 0) return;
      e.preventDefault();
      const next = items[(i + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length];
      next.focus(); next.click();
    });
  }

  $("#makeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.set("lang", $("#lang").value); fd.set("grade", $("#grade").value);
    const src = currentSource();
    if (src === "upload") {
      if (!fileInput.files[0]) return toast("Choose a file first, or try the sample chapter.");
      fd.set("file", fileInput.files[0]);
      if ($("#pFrom").value) fd.set("page_from", $("#pFrom").value);
      if ($("#pTo").value) fd.set("page_to", $("#pTo").value);
    } else if (src === "paste") {
      if (!$("#text").value.trim()) return toast("Paste some text first.");
      fd.set("text", $("#text").value);
    } else {
      if (!$("#topic").value.trim()) return toast("Type a topic first.");
      fd.set("topic", $("#topic").value);
    }
    const btn = $("#makeBtn"); setBusy(btn, true, "Starting…");
    try { const { id } = await api("/api/lessons", { method: "POST", body: fd }); go(`/lesson/${id}`); }
    catch (err) { toast(err.message, 7000); } finally { setBusy(btn, false); }
  });
  async function startSample(btn) {
    setBusy(btn, true, "Starting…");
    try { const { id } = await postJSON("/api/lessons/sample", { lang: $("#lang").value }); go(`/lesson/${id}`); }
    catch (err) { toast(err.message, 7000); } finally { setBusy(btn, false); }
  }
  $("#sampleBtn").addEventListener("click", (e) => startSample(e.currentTarget));
  $("#heroCta").addEventListener("click", (e) => startSample(e.currentTarget));
  $("#heroMake").addEventListener("click", () => $("#studio").scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth" }));

  /* ---------- home: your lessons ---------- */
  function confirmDelete(title) {
    const d = $("#confirm");
    $("#confirmTitle").textContent = `Delete “${title.length > 36 ? title.slice(0, 36) + "…" : title}”?`;
    return new Promise((resolve) => {
      d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
      d.returnValue = "cancel";
      if (d.showModal) d.showModal(); else resolve(window.confirm("Delete this lesson?"));
    });
  }
  async function loadHistory() {
    const ul = $("#history");
    try {
      const rows = await api("/api/lessons");
      $("#shelfCount").hidden = !rows.length; $("#shelfCount").textContent = rows.length;
      if (!rows.length) {
        return fill(ul, h("li", { class: "shelf-empty" }, "No lessons yet. Make one, or ",
          h("button", { type: "button", class: "btn sm", text: "open the sample chapter", onclick: (e) => startSample(e.currentTarget) }), "."));
      }
      fill(ul, rows.map((r) => {
        const li = langInfo(r.lang);
        return h("li", { class: "lrow" },
          h("button", { type: "button", class: "open", onclick: () => go(`/lesson/${r.id}`) },
            h("span", {}, h("span", { class: "t", text: r.title }), h("span", { class: "m", text: `${li.name} · Class ${r.grade} · ${relTime(r.created)}` }))),
          h("button", {
            type: "button", class: "btn sm del", "aria-label": `Delete ${r.title}`, text: "Delete",
            onclick: async () => {
              if (!(await confirmDelete(r.title))) return;
              try {
                await api(`/api/lessons/${r.id}`, { method: "DELETE" });
                if (state.lid === r.id) { state.lid = null; state.lesson = null; }
                toast("Lesson deleted."); loadHistory();
              } catch (err) { toast(err.message, 6000); }
            },
          }));
      }));
    } catch { /* history is optional */ }
  }

  /* ---------- lesson: open, poll, render ---------- */
  async function openLesson(id) {
    clearTimeout(state.poll);
    Object.assign(state, { lid: id, lesson: null, rendered: 0, quiz: null, focused: false });
    fill($("#paras")); ["#gloss", "#gloss2", "#gloss3"].forEach((q) => fill($(q))); $("#glossCard").hidden = true; $("#checkCard").hidden = true;
    $("#lessonError").hidden = true; $("#progress").hidden = true;
    $("#lTitle").textContent = "Opening lesson…"; fill($("#lMeta"));
    fill($("#chatLog"), chatEmpty()); fill($("#suggest")); fill($("#quizBox")); fill($("#progBox"));
    setSub("read");
    $("#listenBtn").disabled = true;
    await pollLesson(true);
  }
  async function pollLesson(first) {
    const id = state.lid;
    try {
      const data = await api(`/api/lessons/${id}`);
      if (id !== state.lid) return;
      renderLesson(data);
      if (data.status === "running") state.poll = setTimeout(pollLesson, 1000);
    } catch (err) {
      if (id !== state.lid) return;
      if (first && /not found/i.test(err.message)) { toast("That lesson is no longer here."); state.lid = null; go("/"); return; }
      $("#lessonError").textContent = err.message; $("#lessonError").hidden = false;
    }
  }

  // Wrap known exam terms in buttons that open a small card.
  function richText(text, terms, local) {
    const items = (terms || []).filter((t) => t.term && (!local || t.native));
    if (!text || !items.length) return [text];
    const parts = items.map((t) => (local ? esc(t.native) + "(?:\\s*[(（]\\s*" + esc(t.term) + "\\s*[)）])?" : esc(t.term)))
      .sort((a, b) => b.length - a.length);
    let re;
    try { re = new RegExp("(?<![\\p{L}\\p{M}\\p{N}])(?:" + parts.join("|") + ")(?![\\p{L}\\p{M}\\p{N}])", "giu"); }
    catch { re = new RegExp("(?:" + parts.join("|") + ")", "gi"); }
    const out = []; let last = 0, m;
    while ((m = re.exec(text))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const hit = local
        ? items.filter((t) => m[0].startsWith(t.native)).sort((a, b) => b.native.length - a.native.length)[0]
        : items.find((t) => t.term.toLowerCase() === m[0].toLowerCase());
      if (!hit) continue;
      out.push(text.slice(last, m.index));
      out.push(h("button", { type: "button", class: "term", "data-term": hit.term, "data-native": hit.native || "", "aria-haspopup": "dialog", text: m[0] }));
      last = m.index + m[0].length;
    }
    out.push(text.slice(last));
    return out;
  }

  function qualityChip(q) {
    const map = {
      ok: ["ok", `✓ Translation checked${q.score != null ? ` · ${Math.round(q.score * 100)}% match` : ""}`],
      review: ["review", "⚠ Please check this with your teacher"],
      demo: ["demo", "Demo · not checked"],
      error: ["error", "✗ Could not translate this part"],
      unchecked: ["unchecked", "Not checked"],
    };
    const [cls, label] = map[q.status] || map.unchecked;
    return h("span", { class: `q ${cls}`, text: label });
  }

  function pairRow(p, i, lang) {
    const li = langInfo(lang);
    const arch = h("button", { type: "button", class: "arch", "aria-label": archLabel(i), onclick: () => {
      if (P.on && P.idx === i && !P.paused) return silence();
      if (textFor(p)) runPlayer(i, true);
    } });
    return h("article", { class: "pair", "data-i": i },
      h("div", { class: "cell en", lang: "en" },
        h("p", { class: "txt" }, richText(p.simple, p.terms, false)),
        h("p", { class: "orig" }, h("b", { text: "Original wording: " }), p.original)),
      h("div", { class: "spine" }, arch),
      h("div", { class: "cell local", lang, dir: rtl(lang) },
        p.translated
          ? h("p", { class: "txt" }, richText(p.translated, p.terms, true))
          : h("p", { class: "txt muted", text: p.error ? `Could not translate: ${p.error}` : "…" })),
      h("div", { class: "pair-foot" },
        h("span", { text: `Paragraph ${i + 1} · page ${p.page}` }), qualityChip(p.quality || {}), h("span", { class: "spacer" }),
        p.translated ? h("button", { class: "mini", type: "button", text: `Listen in ${li.name}`, onclick: () => runPlayer(i, true) }) : null),
    );
  }
  function skeletonRow() {
    const lines = () => [h("div", { class: "skel-line" }), h("div", { class: "skel-line" }), h("div", { class: "skel-line" })];
    return h("div", { class: "pair skeleton", "aria-hidden": "true" },
      h("div", { class: "cell en" }, lines()), h("div", { class: "spine" }), h("div", { class: "cell local" }, lines()));
  }

  function termList(ul, d) {
    fill(ul, d.terms.map((t) => h("li", {}, h("button", { type: "button", class: "term-row", "aria-label": `Hear ${t.term} in ${langInfo(d.lang).name}`, onclick: () => sayOnce(t.native, d.lang, true) },
      h("span", {}, h("span", { class: "n", lang: d.lang, dir: rtl(d.lang), text: t.native }), h("span", { class: "e", text: t.term }))))));
  }
  function renderCheck(d) {
    const n = d.paragraphs.length, card = $("#checkCard");
    card.hidden = !n; if (!n) return;
    const c = { ok: 0, review: 0, error: 0, plain: 0 };
    d.paragraphs.forEach((p) => { const st = (p.quality || {}).status; if (st === "ok") c.ok++; else if (st === "review") c.review++; else if (st === "error") c.error++; else c.plain++; });
    $("#checkText").textContent = d.mock
      ? "Demo mode: these translations are stand-ins, so nothing is checked."
      : `${c.ok} of ${n} paragraph${n === 1 ? "" : "s"} matched when translated back to English.${c.review ? ` ${c.review} need a teacher's look.` : ""}`;
    const kinds = [["ok", "Matches the original"], ["review", "Check with a teacher"], ["error", "Could not translate"], ["plain", "Not checked"]];
    fill($("#checkBar"), kinds.filter(([k]) => c[k]).map(([k]) => h("i", { class: k, style: `flex:${c[k]}` })));
    fill($("#checkLegend"), kinds.filter(([k]) => c[k]).map(([k, label]) => h("li", {}, h("i", { class: k }), label + ": ", h("b", { text: c[k] }))));
  }

  function renderLesson(d) {
    state.lesson = d;
    const li = langInfo(d.lang);
    $("#lTitle").textContent = d.title;
    document.title = `${d.title} · VidyaSetu`;
    fill($("#lMeta"),
      h("span", { class: "chip" }, h("span", { lang: d.lang, text: li.native }), " ", h("small", { text: li.name })),
      h("span", { class: "chip", text: `Class ${d.grade}` }),
      d.total ? h("span", { class: "chip", text: `${d.total} paragraph${d.total === 1 ? "" : "s"}` }) : null,
      d.mock ? h("span", { class: "chip", text: "Demo translation" }) : null);
    const lh = $("#localHead"); lh.lang = d.lang; lh.textContent = `${li.native} · ${li.name}`;
    $("#viewMine").textContent = li.native; $("#viewMine").lang = d.lang; $("#viewMine").setAttribute("aria-label", `${li.name} only`);

    const running = d.status === "running";
    $("#progress").hidden = !running;
    if (running) {
      const step = /Simplif/.test(d.stage) ? 2 : /Index/.test(d.stage) ? 1 : 0;
      $$("#steps li").forEach((x, i) => { x.classList.toggle("done", i < step); x.classList.toggle("now", i === step); });
      const pct = step === 2 && d.total ? 20 + (d.done / d.total) * 80 : step === 1 ? 14 : 6;
      const bar = $(".bar", $("#progress")); $("#progBar").style.width = `${Math.round(pct)}%`; bar.setAttribute("aria-valuenow", Math.round(pct));
      $("#progNote").textContent = step === 2 && d.total
        ? `${d.done} of ${d.total} paragraphs are ready. You can start reading while the rest is made.`
        : d.stage + "…";
    }
    const box = $("#paras");
    $$(".skeleton", box).forEach((s) => s.remove());
    for (let i = state.rendered; i < d.paragraphs.length; i++) box.append(pairRow(d.paragraphs[i], i, d.lang));
    state.rendered = d.paragraphs.length;
    if (running) box.append(skeletonRow());

    ["#gloss", "#gloss2", "#gloss3"].forEach((q) => termList($(q), d));
    $("#glossCard").hidden = !d.terms.length;
    renderCheck(d);
    $("#listenBtn").disabled = !d.paragraphs.some((p) => p.translated);
    $("#lessonError").hidden = d.status !== "error";
    if (d.status === "error") $("#lessonError").textContent = d.error || "Something went wrong while building this lesson.";
    if (d.status === "done") buildSuggestions(d);
    if (!running && !state.focused) { state.focused = true; $("#lTitle").focus({ preventScroll: true }); }
  }

  /* ---------- reading options ---------- */
  function setView(v) {
    state.view = v; setPref("view", v);
    const sheet = $("#sheet");
    sheet.classList.remove("view-both", "view-mine", "view-english"); sheet.classList.add("view-" + v);
    $$("#toolbar [data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === v)));
    if (P.on) { const keep = P.idx; if (textFor(state.lesson.paragraphs[keep])) runPlayer(keep, P.once); else silence(); }
    $$(".pair .arch").forEach((a) => { if (!a.classList.contains("is-playing")) a.setAttribute("aria-label", archLabel(+a.closest(".pair").dataset.i)); });
  }
  $$("#toolbar [data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  function setScale(i) {
    state.scale = Math.max(0, Math.min(SCALES.length - 1, i)); setPref("scale", state.scale);
    $("#sheet").style.setProperty("--scale", SCALES[state.scale]);
    $("#sizeDown").disabled = state.scale === 0; $("#sizeUp").disabled = state.scale === SCALES.length - 1;
  }
  $("#sizeDown").addEventListener("click", () => setScale(state.scale - 1));
  $("#sizeUp").addEventListener("click", () => setScale(state.scale + 1));
  $("#showOrig").addEventListener("change", (e) => { $("#sheet").classList.toggle("show-orig", e.target.checked); setPref("orig", e.target.checked); });

  /* ---------- term card ---------- */
  const pop = $("#termPop");
  let popFrom = null;
  function closePop() { if (!pop.hidden) { pop.hidden = true; if (popFrom) popFrom.focus({ preventScroll: true }); popFrom = null; } }
  document.addEventListener("click", (e) => {
    const t = e.target.closest(".term");
    if (!t) { if (!e.target.closest("#termPop")) { pop.hidden = true; popFrom = null; } return; }
    if (!pop.hidden && popFrom === t) return closePop();
    popFrom = t;
    const lang = t.dataset.lang || (state.lesson ? state.lesson.lang : "hi"), native = t.dataset.native, term = t.dataset.term, hero = !!t.dataset.hero;
    fill(pop,
      native ? h("div", { class: "tp-native", lang, dir: rtl(lang), text: native }) : null,
      h("div", { class: "tp-en", text: term }),
      h("div", { class: "tp-acts" },
        native ? h("button", { class: "mini", type: "button", text: "Hear it", onclick: () => sayOnce(native, lang, true) }) : null,
        hero ? null : h("button", { class: "mini", type: "button", text: "Ask about it", onclick: () => { pop.hidden = true; popFrom = null; setSub("ask"); ask(`What is ${term}?`, false); } }),
        h("button", { class: "mini", type: "button", text: "Close", onclick: closePop })));
    pop.hidden = false;
    const r = t.getBoundingClientRect(), w = pop.offsetWidth;
    pop.style.left = `${Math.max(12, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - w - 12))}px`;
    pop.style.top = `${window.scrollY + r.bottom + 8}px`;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  window.addEventListener("resize", () => { pop.hidden = true; });

  /* ---------- lesson sections ---------- */
  function setSub(name) {
    state.sub = name;
    $$(".sec").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.pane === name)));
    $$("[data-sub]").forEach((s) => (s.hidden = s.dataset.sub !== name));
    if (name !== "read") silence();
    if (name === "quiz" && !state.quiz && !$("#quizBox").children.length) renderQuizStart();
    if (name === "prog") renderProgress();
    if (name === "ask" && state.lesson) setTimeout(() => $("#askInput").focus({ preventScroll: true }), 30);
  }
  $$(".sec").forEach((b) => b.addEventListener("click", () => { setSub(b.dataset.pane); window.scrollTo({ top: 0 }); }));

  /* ---------- ask a doubt ---------- */
  const chatEmpty = () => h("div", { class: "chat-empty" }, h("b", { text: "Ask anything about this lesson" }),
    "Type it, or tap the microphone and speak in your language. Answers come only from your own textbook, with the page number.");
  function buildSuggestions(d) {
    const box = $("#suggest"); if (box.children.length) return;
    if (!d.terms.length) box.append(h("button", { class: "mini", type: "button", text: "What is this lesson about?", onclick: () => ask("What is this lesson about?", false) }));
    d.terms.slice(0, 3).forEach((t) => box.append(h("button", { class: "mini", type: "button", text: `What is ${t.term}?`, onclick: () => ask(`What is ${t.term}?`, false) })));
  }
  $("#askForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#askInput").value.trim(); if (v) ask(v, false); });

  async function ask(q, fromVoice) {
    if (!state.lid || !state.lesson) return;
    const lang = state.lesson.lang, li = langInfo(lang), log = $("#chatLog");
    if (log.firstElementChild && log.firstElementChild.classList.contains("chat-empty")) log.replaceChildren();
    $("#askInput").value = "";
    log.append(h("div", { class: "msg me", text: q }));
    const bot = h("div", { class: "msg bot", "aria-label": "Answer" }, h("span", { class: "dots", "aria-label": "Thinking" }, h("span"), h("span"), h("span")));
    log.append(bot); bot.scrollIntoView({ block: "end", behavior: reduceMotion() ? "auto" : "smooth" });
    $("#askBtn").disabled = true;
    try {
      const r = await postJSON(`/api/lessons/${state.lid}/ask`, { question: q });
      const pages = [...new Set(r.sources.map((x) => x.page))].join(", ");
      bot.classList.toggle("notfound", !r.found);
      fill(bot,
        h("div", { class: "big", lang, dir: rtl(lang), text: r.answer }),
        h("div", { class: "small", text: r.answer_en }),
        r.sources.length ? h("div", { class: "src" }, `From your textbook, page ${pages}: “${r.sources[0].snippet}${r.sources[0].snippet.length >= 220 ? "…" : ""}”`) : null,
        h("div", { class: "src" }, h("button", { class: "mini", type: "button", text: `Listen in ${li.name}`, onclick: () => sayOnce(r.answer, lang, true) })));
      if (fromVoice) sayOnce(r.answer, lang, true);
    } catch (err) {
      bot.classList.add("notfound");
      bot.replaceChildren(h("span", { text: err.message }));
    } finally { $("#askBtn").disabled = false; bot.scrollIntoView({ block: "end", behavior: reduceMotion() ? "auto" : "smooth" }); }
  }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("#micBtn");
  let rec = null;
  if (!SR) mic.hidden = true;
  mic.addEventListener("click", () => {
    if (!state.lesson) return;
    if (rec) { rec.stop(); return; }
    silence();
    rec = new SR();
    rec.lang = langInfo(state.lesson.lang).speech; rec.interimResults = false; rec.maxAlternatives = 1;
    mic.classList.add("rec"); mic.textContent = "Stop"; mic.setAttribute("aria-label", "Stop listening");
    $("#askInput").placeholder = "Listening… speak your doubt";
    const done = () => { rec = null; mic.classList.remove("rec"); mic.textContent = "Mic"; mic.setAttribute("aria-label", "Ask by voice"); $("#askInput").placeholder = "Type your doubt"; };
    rec.onresult = (ev) => { const t = ev.results[0][0].transcript; $("#askInput").value = t; ask(t, true); };
    rec.onerror = (ev) => { toast(ev.error === "not-allowed" ? "Allow microphone access to ask by voice." : "Could not hear you. Please try again."); };
    rec.onend = done;
    try { rec.start(); } catch { done(); }
  });

  /* ---------- quiz ---------- */
  function renderQuizStart() {
    state.quiz = null;
    fill($("#quizBox"), h("div", { class: "quiz-start" },
      h("h2", { text: "Test yourself" }),
      h("p", { text: "Five questions made from this lesson, in your language with English underneath. Press 1 to 4 on a keyboard to answer." }),
      h("button", { class: "btn primary", type: "button", id: "startQuiz", text: "Start quiz", onclick: startQuiz })));
  }
  async function startQuiz() {
    const btn = $("#startQuiz"); setBusy(btn, true, "Making questions…");
    try {
      const { questions } = await postJSON(`/api/lessons/${state.lid}/quiz`, { n: 5 });
      state.quiz = { qs: questions, i: 0, score: 0, results: [], missed: [], full: true, pick: null };
      showQuestion();
    } catch (err) { setBusy(btn, false); toast(err.message, 7000); }
  }
  function showQuestion() {
    const Q = state.quiz, q = Q.qs[Q.i], lang = state.lesson.lang;
    const last = Q.i + 1 === Q.qs.length;
    const next = h("button", { class: "btn primary", type: "button", text: last ? "See my score" : "Next question" });
    const explain = h("div", { class: "explain", hidden: true, role: "status" });
    let answered = false;
    const btns = q.options.map((o, idx) => h("button", { class: "opt", type: "button" },
      h("span", { class: "k", "aria-hidden": "true", text: "ABCD"[idx] }),
      h("span", { class: "l", lang, dir: rtl(lang), text: o.local }), h("span", { class: "e", text: o.en })));
    const pick = (idx) => {
      if (answered || !btns[idx]) return;
      answered = true;
      const ok = idx === q.answer_index;
      btns.forEach((x, j) => { x.disabled = true; if (j === q.answer_index) x.classList.add("right"); });
      if (ok) Q.score++; else { btns[idx].classList.add("wrong"); Q.missed.push(q); }
      Q.results.push(ok);
      explain.className = `explain ${ok ? "good" : "bad"}`;
      fill(explain, h("b", { text: ok ? "✓ Correct! " : "✗ Not quite. " }), h("span", { lang, dir: rtl(lang), text: q.explanation.local }), h("div", { class: "muted", text: q.explanation.en }));
      explain.hidden = false; next.parentElement.hidden = false;
      $$(".qdots i", $("#quizBox"))[Q.i].className = ok ? "right" : "wrong";
      next.focus({ preventScroll: true });
    };
    btns.forEach((b, idx) => b.addEventListener("click", () => pick(idx)));
    Q.pick = pick;
    next.addEventListener("click", () => { Q.i++; Q.i < Q.qs.length ? showQuestion() : finishQuiz(); });
    fill($("#quizBox"), h("div", { class: "qcard" },
      h("div", { class: "qhead" }, h("span", { text: `Question ${Q.i + 1} of ${Q.qs.length}` }),
        h("span", { class: "qdots", "aria-hidden": "true" }, Q.qs.map((_, k) => h("i", { class: k < Q.i ? (Q.results[k] ? "right" : "wrong") : k === Q.i ? "on" : "" })))),
      h("div", { class: "qtext", lang, dir: rtl(lang), text: q.question.local }),
      h("div", { class: "qtext-en", text: q.question.en }),
      h("div", { class: "opts" }, btns), explain, h("div", { class: "qnext", hidden: true }, next)));
  }
  document.addEventListener("keydown", (e) => {
    if (state.sub !== "quiz" || !state.quiz || !state.quiz.pick || $("#lessonView").hidden) return;
    if (e.target.closest("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (/^[1-4]$/.test(e.key)) state.quiz.pick(+e.key - 1);
  });
  async function finishQuiz() {
    const Q = state.quiz; Q.pick = null;
    const { score, qs, full, missed } = Q, lang = state.lesson.lang;
    if (full) { try { await postJSON(`/api/lessons/${state.lid}/results`, { score, total: qs.length }); } catch { /* non-fatal */ } }
    const msg = score === qs.length ? "Perfect score."
      : score >= qs.length / 2 ? "Good work. Look at the ones you missed, then try again."
      : "Keep going. Read the lesson again, then try the quiz.";
    fill($("#quizBox"), h("div", { class: "score" },
      h("div", { class: "big-n", text: `${score} / ${qs.length}` }), h("p", { text: msg }),
      missed.length ? h("div", { class: "missed" }, h("b", { text: "To look at again" }),
        h("ul", {}, missed.map((q) => h("li", {}, h("span", { lang, dir: rtl(lang), text: q.question.local }), h("div", { class: "muted", text: `Answer: ${q.options[q.answer_index].en}` }))))) : null,
      h("div", { class: "acts" },
        missed.length ? h("button", { class: "btn quiet", type: "button", text: "Retry the ones I missed", onclick: () => { state.quiz = { qs: missed, i: 0, score: 0, results: [], missed: [], full: false, pick: null }; showQuestion(); } }) : null,
        h("button", { class: "btn quiet", type: "button", text: "Read the lesson", onclick: () => setSub("read") }),
        h("button", { class: "btn primary", type: "button", text: "New quiz", onclick: () => { renderQuizStart(); startQuiz(); } }))));
  }

  /* ---------- progress ---------- */
  async function renderProgress() {
    const box = $("#progBox"); if (!state.lid) return;
    try {
      const rows = await api(`/api/lessons/${state.lid}/results`);
      if (!rows.length) return fill(box, h("div", { class: "empty-card" }, h("h2", { text: "No quiz scores yet" }),
        h("p", { class: "muted", text: "Take the quiz and your scores will show up here." }),
        h("button", { class: "btn primary", type: "button", text: "Take the quiz", onclick: () => setSub("quiz") })));
      const best = Math.max(...rows.map((r) => Math.round((r.score / r.total) * 100)));
      fill(box,
        h("div", {}, h("h2", { text: "My progress" }), h("div", { class: "stats" },
          h("div", { class: "stat" }, h("b", { text: rows.length }), h("span", { text: rows.length === 1 ? "quiz taken" : "quizzes taken" })),
          h("div", { class: "stat" }, h("b", { text: `${best}%` }), h("span", { text: "best score" })),
          h("div", { class: "stat" }, h("b", { text: `${rows[0].score}/${rows[0].total}` }), h("span", { text: "latest" }))),
          rows.length < 2 ? null : h("div", { class: "chart", role: "img", "aria-label": "Your last quiz scores, oldest to newest" },
            rows.slice(0, 10).reverse().map((r) => { const pc = Math.round((r.score / r.total) * 100); return h("div", { class: `col${pc === best ? " best" : ""}` }, h("span", { text: `${r.score}/${r.total}` }), h("i", { style: `height:${Math.max(4, pc * 0.9)}%` })); }))),
        h("ul", { class: "attempts" }, rows.map((r) => h("li", {},
          h("span", { text: new Date(r.ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) }),
          h("span", { class: "meter" }, h("i", { style: `width:${Math.round((r.score / r.total) * 100)}%` })),
          h("b", { text: `${r.score}/${r.total}` })))));
    } catch (err) { fill(box, h("p", { text: err.message })); }
  }

  /* ---------- teacher mode ---------- */
  $("#teachForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const langs = $$("#tLangs input:checked").map((i) => i.value);
    const text = $("#tText").value.trim();
    if (!text) return toast("Paste the worksheet first.");
    if (!langs.length) return toast("Pick at least one language.");
    setPref("tlangs", langs);
    const btn = $("#tBtn"); setBusy(btn, true, "Converting…");
    fill($("#tOut"), h("p", { class: "t-empty" }, h("span", { class: "spin" }), `Converting into ${langs.length} language${langs.length === 1 ? "" : "s"}…`));
    try {
      const out = await postJSON("/api/teacher", { text, langs, grade: +$("#tGrade").value, simplify: $("#tSimple").checked });
      fill($("#tOut"), langs.map((code) => teacherCard(code, out[code])));
    } catch (err) { fill($("#tOut"), h("p", { class: "t-empty", text: "Nothing was converted. Check the message and try again." })); toast(err.message, 7000); }
    finally { setBusy(btn, false); }
  });
  $("#tSample").addEventListener("click", () => {
    $("#tText").value = "Plants make their own food. This process is called photosynthesis.\n\n" +
      "Leaves contain a green substance called chlorophyll. Chlorophyll captures energy from sunlight.\n\n" +
      "Oxygen is released into the air as a by-product. Animals and humans breathe in this oxygen.";
    $("#tText").focus();
  });
  function teacherCard(code, r) {
    const li = langInfo(code);
    const download = () => {
      const a = h("a", { href: URL.createObjectURL(new Blob([r.text], { type: "text/plain;charset=utf-8" })), download: `worksheet-${code}.txt` });
      document.body.append(a); a.click(); a.remove();
    };
    return h("section", { class: "t-card" },
      h("div", { class: "t-head" },
        h("h2", {}, `${li.name} `, h("span", { class: "muted", lang: code, text: li.native })),
        h("span", { class: "acts" },
          r.review ? h("span", { class: "q review", text: `⚠ ${r.review} to review` }) : null,
          h("button", { class: "mini", type: "button", text: "Copy", onclick: async () => { try { await navigator.clipboard.writeText(r.text); toast("Copied"); } catch { toast("Copy failed. Select the text and copy it."); } } }),
          h("button", { class: "mini", type: "button", text: "Download .txt", onclick: download }))),
      h("div", { class: "body", lang: code, dir: rtl(code), text: r.text || "(nothing could be translated)" }));
  }

  /* ---------- start ---------- */
  async function init() {
    P.rate = RATES.includes(pref("rate", 1)) ? pref("rate", 1) : 1;
    state.cfg = await api("/api/config");
    state.cfg.languages.forEach((l) => $("#langPick").append(h("button", { type: "button", role: "radio", class: "lang-tile", "data-code": l.code, "aria-checked": "false", onclick: () => setLang(l.code) },
      h("span", { class: "n", lang: l.code, text: l.native }), h("span", { class: "e", text: l.name }))));
    radioKeys($("#langPick"));
    const g = pref("grade", 6);
    for (let n = 1; n <= 12; n++) {
      $("#gradePick").append(h("button", { type: "button", role: "radio", class: "grade-btn", "data-n": n, "aria-checked": "false", "aria-label": `Class ${n}`, text: n, onclick: () => setGrade(n) }));
      $("#tGrade").append(h("option", { value: n, text: `Class ${n}`, selected: n === g }));
    }
    radioKeys($("#gradePick"));
    const gl = state.cfg.languages.some((l) => l.code === pref("lang", "hi")) ? pref("lang", "hi") : "hi";
    setLang(gl); setGrade(Math.min(12, Math.max(1, +g || 6)));
    const badge = $("#modeBadge");
    badge.hidden = false; badge.className = `badge ${state.cfg.mode}`; badge.textContent = state.cfg.label;
    const banner = $("#demoBanner");
    banner.hidden = state.cfg.mode === "live";
    banner.textContent = state.cfg.mode === "partial"
      ? "Some steps are still using placeholder AI. Check the provider settings in your .env file."
      : "Offline demo mode: no AI keys are set, so the AI steps use stand-ins. The sample chapter is pre-translated into all 10 languages. Add a Sarvam AI or IBM watsonx key in .env for live AI.";
    const saved = pref("tlangs", null);
    state.cfg.languages.forEach((l, i) =>
      $("#tLangs").append(h("label", { class: "lchip" }, h("input", { type: "checkbox", value: l.code, checked: saved ? saved.includes(l.code) : i < 2 }), h("span", { lang: l.code, text: l.native }))));
    setSource(["upload", "paste", "topic"].includes(pref("src", "upload")) ? pref("src", "upload") : "upload");
    const v = pref("view", "both"); setView(["both", "mine", "english"].includes(v) ? v : "both");
    setScale(Math.min(SCALES.length - 1, Math.max(0, pref("scale", 1))));
    $("#showOrig").checked = !!pref("orig", false); $("#sheet").classList.toggle("show-orig", $("#showOrig").checked);
    if (window.speechSynthesis) speechSynthesis.onvoiceschanged = () => {};
    setListenLabel();
    await route();
  }
  init().catch((err) => toast(err.message, 8000));
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
