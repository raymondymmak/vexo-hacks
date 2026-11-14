/* Remember — client. Vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const first = (n) => (n || "").split(/\s+/)[0] || "Someone";
  const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
  const hue = (s) => { let h = 0; for (const c of s || "") h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  // Initials fall back to a bold color from a fixed palette (stable per name) when there's no photo.
  const PALETTE = ["#ff6b4a", "#f59e0b", "#22b573", "#1c9bff", "#7b61ff", "#ff4fa3", "#0fb5a6", "#4361ee"];
  const avatarColor = (n) => PALETTE[hue(n) % PALETTE.length];
  const icon = (name, extra = "") => `<svg class="ic ${extra}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const SOURCE_ICON = { handshake: "swap", intro: "intro", match: "link", conversation: "mic" };
  const KIND_ICON = { handshake: "swap", intro: "intro", need_offer: "link" };
  const SOCIAL_ICON = { linkedin: "linkedin", github: "github", twitter: "x", website: "globe" };

  /** Photo (LinkedIn upload/link, GitHub) when we have one, colored initials underneath as the fallback. */
  function avatar(p, { size = "", ring = "", badge = "", edit = null } = {}) {
    const img = p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">` : "";
    const b = badge ? `<span class="av-badge ${esc(badge)}">${icon(SOURCE_ICON[badge] || "mic")}</span>` : "";
    const av = `<span class="avatar ${size} ${ring ? "ring " + esc(ring) : ""}" style="--av:${avatarColor(p.name)}"><b>${esc(initials(p.name))}</b>${img}${b}</span>`;
    if (edit == null) return av;
    return `<span class="av-wrap">${av}<button class="av-edit" data-photo="${edit}" data-name="${esc(p.name)}" title="Change photo" aria-label="Change photo for ${esc(p.name)}">${icon("camera")}</button></span>`;
  }

  const LED_LABEL = { idle: "Idle", listening: "Listening", saved: "Card saved", matched: "New intro" };
  const SOURCE = {
    handshake: { label: "Shook hands", cls: "handshake" },
    intro: { label: "Introduced", cls: "intro" },
    match: { label: "Matched", cls: "match" },
    conversation: { label: "Conversation", cls: "conversation" },
  };
  const SOCIAL_LABEL = { linkedin: "LinkedIn", github: "GitHub", twitter: "X", website: "Website" };
  const VIEW_TITLE = {
    chats: ["Conversations.", "Every word your band heard, kept like messages."],
    contacts: ["Contacts.", "Everyone you met, ready to save to your phone."],
    room: ["Room map.", "Watch the whole room connect."],
  };

  let state = { bands: [], people: [], contacts: [], matches: [], ai: {} };
  let presets = { transcripts: [], intros: [] };
  let filter = "all";
  let chatBand = null, chatPerson = null, threadsCache = [], threadsReq = 0, threadReq = 0;
  let seenMatches = null; // match ids already drawn on the map (null until the map is first visible)
  const freshContacts = new Set();
  const flashPeople = new Set();
  const mapOpen = () => $("#view-room").classList.contains("active");

  // ============================================================ API
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `Request failed (${res.status})`);
    }
    return data;
  }

  async function refresh() { state = await api("/api/state"); render(); }
  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refresh().catch((e) => toast("Couldn't refresh", e.message, "err")), 60);
  }

  // ============================================================ live events
  function connect() {
    const es = new EventSource("/api/events");
    const pill = $("#connPill");
    es.onopen = () => { pill.classList.add("live"); pill.querySelector("b").textContent = "Live"; };
    es.onerror = () => { pill.classList.remove("live"); pill.querySelector("b").textContent = "Reconnecting"; };
    const on = (type, fn) => es.addEventListener(type, (e) => { try { fn(JSON.parse(e.data)); } catch (err) { console.error(err); } });

    on("hello", () => scheduleRefresh());
    on("band_status", (d) => {
      const i = state.bands.findIndex((b) => b.id === d.band.id);
      if (i >= 0) state.bands[i] = d.band; else state.bands.push(d.band);
      renderBands();
      renderRoomStates();
      if (d.profile_updated) scheduleRefresh();
    });
    on("contact_created", (d) => {
      freshContacts.add(d.contact.id);
      if (d.band_id === chatBand) chatPerson = d.contact.person_id; // open the new conversation
      toast(`Saved ${d.contact.name}`, `New card in ${first(d.contact.owner_name)}'s phone`, "ok");
      scheduleRefresh();
    });
    on("handshake_swap", (d) => {
      d.contacts.forEach((c) => freshContacts.add(c.id));
      if (!mapOpen()) toast(d.headline, "Both phones got the other's card and a private note", "ok");
      banner(d.headline, "Cards swapped. Each side keeps a private note on why they met.", "handshake");
      scheduleRefresh();
    });
    on("room_match", (d) => {
      [d.a && d.a.id, d.b && d.b.id].forEach((id) => id && flashPeople.add(id));
      setTimeout(() => { flashPeople.clear(); renderRoomStates(); }, 4000);
      if (!mapOpen()) toast(d.headline, `Only ${first(d.a && d.a.name)} and ${first(d.b && d.b.name)} are notified`, d.kind === "intro" ? "intro" : "match");
      banner(d.headline, `Only ${first(d.a && d.a.name)} and ${first(d.b && d.b.name)} are notified`, d.kind);
      scheduleRefresh();
    });
    on("room_loaded", () => { seenMatches = null; toast("Sample room loaded", "Akim, Priya and Sam are wearing bands A, B and C", "ok"); scheduleRefresh(); });
    on("conversation_deleted", () => scheduleRefresh());
    on("person_updated", () => scheduleRefresh());
    on("session_reset", () => { seenMatches = null; freshContacts.clear(); toast("All data cleared"); scheduleRefresh(); });
  }

  // ============================================================ render
  function render() {
    renderBands();
    renderChatBands();
    loadThreads().catch((e) => console.warn(e));
    renderFilters();
    renderCards();
    renderRoom();
    renderSelects();
    $("#aiMode").textContent = state.ai.enabled ? `AI: OpenAI (${state.ai.model})` : "AI: offline mode (no API key)";
  }

  const bandName = (b) => (b.owner ? first(b.owner.name) : "Not paired");

  function renderBands() {
    $("#bands").innerHTML = state.bands.map((b) => `
      <div class="band ${esc(b.led)}" data-id="${esc(b.id)}">
        ${avatar(b.owner || { name: `Band ${b.id}`, photo: "" }, { size: "md", ring: b.led })}
        <div class="band-meta"><b>${esc(bandName(b))}</b><small>${icon("band")}Band ${esc(b.id)} · ${esc(LED_LABEL[b.led] || b.led)}</small></div>
      </div>`).join("");
  }

  function renderFilters() {
    const owned = state.bands.filter((b) => b.owner);
    if (filter !== "all" && !owned.some((b) => b.id === filter)) filter = "all";
    if (!state.contacts.length) { $("#filters").innerHTML = ""; return; }
    const chips = [{ id: "all", label: "Everyone" }].concat(owned.map((b) => ({ id: b.id, label: `${first(b.owner.name)}'s phone` })));
    $("#filters").innerHTML = chips.map((c) =>
      `<button class="chip ${filter === c.id ? "active" : ""}" data-filter="${esc(c.id)}">${esc(c.label)}</button>`).join("");
  }

  // ============================================================ conversations (chat history)
  function clock(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === now.toDateString() ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${t}`;
  }
  function spoken(utterances) {
    const words = utterances.reduce((n, u) => n + u.text.split(/\s+/).length, 0);
    const secs = Math.max(10, Math.round(words / 2.5)); // ~150 words per minute
    return secs < 60 ? `~${secs} sec` : `~${Math.round(secs / 60)} min`;
  }

  function renderChatBands() {
    const owned = state.bands.filter((b) => b.owner);
    if (!owned.some((b) => b.id === chatBand)) { chatBand = owned[0] ? owned[0].id : null; chatPerson = null; }
    $("#chatBands").innerHTML = owned.map((b) => `
      <button class="chip ${b.id === chatBand ? "active" : ""}" data-band="${esc(b.id)}">
        <i class="led ${esc(b.led)}"></i>${esc(first(b.owner.name))}'s band</button>`).join("");
  }

  async function loadThreads() {
    const empty = state.people.length === 0;
    $("#chatsEmpty").classList.toggle("hidden", !empty);
    $("#chat").classList.toggle("hidden", empty);
    $("#chatBands").classList.toggle("hidden", empty);
    if (empty || !chatBand) { $("#chatList").innerHTML = ""; $("#thread").innerHTML = ""; return; }
    const req = ++threadsReq;
    const list = await api(`/api/bands/${chatBand}/threads`);
    if (req !== threadsReq) return;
    threadsCache = list;
    if (!list.some((t) => t.person_id === chatPerson)) chatPerson = list[0] ? list[0].person_id : null;
    renderChatList();
    await loadThread();
  }

  function renderChatList() {
    if (!threadsCache.length) {
      $("#chatList").innerHTML = `<p class="chat-none">No conversations on this band yet.<br>Try Demo controls → Have a conversation.</p>`;
      return;
    }
    $("#chatList").innerHTML = threadsCache.map((t) => `
      <button class="chat-item ${t.person_id === chatPerson ? "active" : ""}" data-person="${t.person_id}">
        ${avatar({ name: t.name, photo: t.photo }, { size: "sm", badge: t.source })}
        <span class="ci-main">
          <span class="ci-top"><b>${esc(t.name)}</b><time>${esc(clock(t.last_at))}</time></span>
          <span class="ci-prev">${esc(t.preview)}</span>
        </span>
      </button>`).join("");
  }

  async function loadThread() {
    if (!chatPerson) { $("#thread").innerHTML = `<p class="thread-empty">Pick a conversation</p>`; return; }
    const req = ++threadReq;
    const d = await api(`/api/bands/${chatBand}/threads/${chatPerson}`);
    if (req !== threadReq) return;
    renderThread(d);
  }

  // Lines where someone commits to something get a small "Promise" tag in the transcript.
  const PROMISE_RE = /\b(I'll|I will|I'm going to|let me|we'll|I can send)\b/i;

  /** Decorative waveform for a recording, stable per recording id. */
  function waveform(seed, n = 36) {
    let x = (seed * 7919 + 104729) % 233280, out = "";
    for (let i = 0; i < n; i++) {
      x = (x * 9301 + 49297) % 233280;
      const env = Math.sin((i / (n - 1)) * Math.PI) * 0.6 + 0.4; // louder in the middle
      out += `<i style="height:${Math.round(18 + (x / 233280) * 82 * env)}%"></i>`;
    }
    return out;
  }

  function renderThread(d) {
    const c = d.contact;
    const me = d.owner || { name: "You", photo: "" };
    const sub = [c.role, c.company].filter(Boolean).join(" · ");
    const promises = promiseList(c);
    const body = d.items.map((it) => {
      if (it.type === "event") {
        return `<div class="sys ${esc(it.kind)}"><span class="sys-ic">${icon(KIND_ICON[it.kind] || "link")}</span>${esc(it.text)}<time>${esc(clock(it.created_at))}</time></div>`;
      }
      const lines = it.utterances.length
        ? it.utterances.map((u, i) => {
          const mine = u.who === "me";
          const cont = i > 0 && it.utterances[i - 1].who === u.who;
          return `<div class="line ${mine ? "me" : "them"}${cont ? " cont" : ""}">
            <span class="line-av">${cont ? "" : avatar(mine ? me : c, { size: "xs" })}</span>
            <div class="line-body"><p class="lb">${esc(u.text)}</p>${PROMISE_RE.test(u.text) ? `<span class="ptag">${icon("check")}Promise</span>` : ""}</div>
          </div>`;
        }).join("")
        : `<p class="lb muted">${esc(it.summary || "Recording saved")}</p>`;
      return `<section class="rec">
        <header class="rec-top">
          <span class="rec-ic">${icon("mic")}</span>
          <div class="rec-meta"><b>Recorded on Band ${esc(d.band_id)}</b><small>${esc(clock(it.created_at))} · ${esc(spoken(it.utterances))}</small></div>
          <span class="wave" aria-hidden="true">${waveform(it.id)}</span>
          <button class="link-btn" data-del="${it.id}">${icon("trash")}Delete</button>
        </header>
        <div class="lines">${lines}</div>
      </section>`;
    }).join("");
    $("#thread").innerHTML = `
      <header class="thread-head">
        <button class="link back" data-back aria-label="Back to conversations">${icon("back")}</button>
        ${avatar(c, { size: "md", edit: c.person_id })}
        <div class="th-id"><b>${esc(c.name)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</div>
        <a class="btn primary small download" href="/api/contacts/${c.id}/vcard" download>${icon("phone")}Save to phone</a>
      </header>
      <div class="thread-body" id="threadBody">
        <div class="pinned">
          <div class="label">${icon("spark")}Why you met</div>
          <div class="hook">${esc(c.why_remember || c.match_reason || "Met at the event")}</div>
          ${promises ? `<ul class="promises">${promises}</ul>` : ""}
        </div>
        ${body || `<p class="sys">No recording yet. You were connected through the room.</p>`}
      </div>`;
    const tb = $("#threadBody");
    tb.scrollTop = tb.scrollHeight;
  }

  function promiseList(c) {
    return (c.promises_made || []).map((p) => `
      <li class="${p.by === "me" ? "mine" : ""}">${icon("check")}<span class="who ${p.by === "me" ? "me" : ""}">${p.by === "me" ? "You" : esc(first(c.name))}</span>${esc(p.text)}</li>`).join("");
  }

  function cardHtml(c) {
    const src = SOURCE[c.source] || SOURCE.conversation;
    const sub = [c.role, c.company].filter(Boolean).join(" · ");
    const links = Object.entries(c.socials || {}).filter(([, v]) => v)
      .map(([k, v]) => `<a class="s-${esc(k)}" href="${esc(/^https?:/.test(v) ? v : "https://" + v)}" target="_blank" rel="noopener">${icon(SOCIAL_ICON[k] || "globe")}${esc(SOCIAL_LABEL[k] || k)}</a>`);
    if (c.email) links.unshift(`<a class="s-mail" href="mailto:${esc(c.email)}">${icon("mail")}${esc(c.email)}</a>`);
    const why = c.why_remember || c.match_reason || "Met at the event";
    const detail = c.conversation_summary || (c.match_reason && c.match_reason !== why ? c.match_reason : "");
    const promises = promiseList(c);
    return `
      <article class="card ${freshContacts.has(c.id) ? "fresh" : ""}">
        <div class="card-top">
          ${avatar(c, { edit: c.person_id })}
          <div class="card-id">
            <h3>${esc(c.name)}</h3>
            ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}
          </div>
          <span class="badge ${src.cls}">${icon(SOURCE_ICON[c.source] || "mic")}${src.label}</span>
        </div>
        <div class="why ${src.cls}">
          <div class="label">${icon("spark")}Why you met</div>
          <div class="hook">${esc(why)}</div>
          ${detail ? `<p class="summary">${esc(detail)}</p>` : ""}
        </div>
        ${promises ? `<div><div class="section-label">Promises</div><ul class="promises">${promises}</ul></div>` : ""}
        ${links.length ? `<div class="socials">${links.join("")}</div>` : ""}
        <div class="card-actions">
          <span class="owner">${icon("band")}In ${esc(first(c.owner_name))}'s phone</span>
          <a class="btn primary small" href="/api/contacts/${c.id}/vcard" download>${icon("phone")}Save to phone</a>
        </div>
      </article>`;
  }

  function renderCards() {
    const list = filter === "all" ? state.contacts : state.contacts.filter((c) => c.owner_band_id === filter);
    $("#cards").innerHTML = list.map(cardHtml).join("");
    $("#contactsEmpty").classList.toggle("hidden", state.contacts.length > 0);
    setTimeout(() => { freshContacts.clear(); $$(".card.fresh").forEach((el) => el.classList.remove("fresh")); }, 2600);
  }

  // ============================================================ isometric room map
  const NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  const pts = (arr) => arr.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  let geom = null;

  /** Cells covered by furniture (stage, booths, tables) in the room layout. */
  function blockedCells(layout) {
    const s = new Set();
    for (const z of (layout && layout.zones) || []) {
      for (let c = z.c0; c <= z.c1; c++) for (let r = z.r0; r <= z.r1; r++) s.add(c + "," + r);
    }
    return s;
  }

  /** Layout seats first (the sample room), then spread anyone else around a ring on free tiles. */
  function placePeople(people, N, layout) {
    const taken = blockedCells(layout), cells = {};
    const seats = (layout && layout.seats) || {};
    const rest = [];
    people.forEach((p) => {
      const s = seats[String(p.id)];
      if (s && !taken.has(s[0] + "," + s[1]) && s[0] < N && s[1] < N) { taken.add(s[0] + "," + s[1]); cells[p.id] = { c: s[0], r: s[1] }; }
      else rest.push(p);
    });
    const center = (N - 1) / 2;
    const R = (N / 2 - 1.3) * Math.SQRT2;
    rest.forEach((p, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(1, rest.length);
      const sx = rest.length > 1 || layout ? Math.cos(a) : 0, sy = rest.length > 1 || layout ? Math.sin(a) : 0;
      let c = Math.min(N - 1, Math.max(0, Math.round(center + (R * (sx + sy)) / Math.SQRT2)));
      let r = Math.min(N - 1, Math.max(0, Math.round(center + (R * (sy - sx)) / Math.SQRT2)));
      search: for (let d = 0; d < N; d++) {
        for (let dc = -d; dc <= d; dc++) for (let dr = -d; dr <= d; dr++) {
          const cc = c + dc, rr = r + dr;
          if (cc < 0 || rr < 0 || cc >= N || rr >= N || taken.has(cc + "," + rr)) continue;
          c = cc; r = rr; break search;
        }
      }
      taken.add(c + "," + r);
      cells[p.id] = { c, r };
    });
    return cells;
  }

  /** An extruded block covering cells c0..c1 × r0..r1, `hgt` px tall. Returns the centre of its top face. */
  function isoBox(g, iso, c0, r0, c1, r1, hgt, cls, inset = 0, color = null) {
    const A = iso(c0 + inset, r0 + inset), B = iso(c1 + 1 - inset, r0 + inset);
    const C = iso(c1 + 1 - inset, r1 + 1 - inset), D = iso(c0 + inset, r1 + 1 - inset);
    const up = (p) => ({ x: p.x, y: p.y - hgt });
    const paint = (el, shade) => { if (color) { el.style.fill = color; el.style.fillOpacity = shade; } return el; };
    paint(svg("polygon", { class: `fx fx-l ${cls}`, points: pts([up(D), up(C), C, D]) }, g), 0.78);
    paint(svg("polygon", { class: `fx fx-r ${cls}`, points: pts([up(C), up(B), B, C]) }, g), 0.62);
    paint(svg("polygon", { class: `fx fx-top ${cls}`, points: pts([up(A), up(B), up(C), up(D)]) }, g), 1);
    return { x: (A.x + C.x) / 2, y: (A.y + C.y) / 2 - hgt };
  }

  function drawZone(z, g, iso, tw, th, H, labels) {
    let top;
    if (z.kind === "stage") {
      top = isoBox(g, iso, z.c0, z.r0, z.c1, z.r1, th * 0.38, "stage", 0.04);
    } else if (z.kind === "booth" || z.kind === "bar") {
      top = isoBox(g, iso, z.c0, z.r0, z.c1, z.r1, th * 0.55, z.kind, 0.14);
      // tall banner in the booth's back corner, in the company color
      isoBox(g, iso, z.c0, z.r0, z.c0, z.r0, H * 1.05, "banner", 0.3, z.color || "#8e8e93");
    } else if (z.kind === "table") {
      const T = iso(z.c0 + 0.5, z.r0 + 0.5), rx = tw * 0.3, ry = th * 0.3, hgt = th * 0.5;
      svg("ellipse", { class: "fx fx-l", cx: T.x, cy: T.y, rx, ry }, g);
      svg("rect", { class: "fx fx-l", x: T.x - rx, y: T.y - hgt, width: rx * 2, height: hgt }, g);
      svg("ellipse", { class: "fx fx-top", cx: T.x, cy: T.y - hgt, rx, ry }, g);
      return;
    }
    if (z.label && top) {
      const lab = document.createElement("div");
      lab.className = `zlabel ${z.kind}`;
      lab.style.left = `${top.x}px`;
      lab.style.top = `${top.y}px`;
      lab.innerHTML = `${z.color ? `<i style="background:${esc(z.color)}"></i>` : ""}${esc(z.label)}`;
      labels.appendChild(lab);
    }
  }

  /** Push name labels down until they stop overlapping avatars, zone labels and each other. */
  function declutterLabels(orbs) {
    const roomBox = $("#room").getBoundingClientRect();
    const rel = (el) => { const b = el.getBoundingClientRect(); return { l: b.left - roomBox.left, t: b.top - roomBox.top, r: b.right - roomBox.left, b: b.bottom - roomBox.top }; };
    const placed = orbs.concat($$("#labels .zlabel").map(rel));
    const hits = (a, o) => !(a.r < o.l || a.l > o.r || a.b < o.t || a.t > o.b);
    $$("#labels .plabel").map((el) => ({ el, box: rel(el) })).sort((a, b) => a.box.t - b.box.t).forEach(({ el, box }) => {
      let shift = 0;
      for (let k = 0; k < 6; k++) {
        const hit = placed.find((o) => hits(box, o));
        if (!hit) break;
        const d = hit.b - box.t + 2;
        if (shift + d > 46) break;
        shift += d; box.t += d; box.b += d;
      }
      if (shift) el.style.top = `${parseFloat(el.style.top) + shift}px`;
      // keep labels inside the room frame
      const nudge = box.l < 4 ? 4 - box.l : box.r > roomBox.width - 4 ? roomBox.width - 4 - box.r : 0;
      if (nudge) { el.style.left = `${parseFloat(el.style.left) + nudge}px`; box.l += nudge; box.r += nudge; }
      placed.push(box);
    });
  }

  function renderRoom() {
    const room = $("#room");
    if (!room.clientWidth) return; // hidden: draw (and animate new lines) once it's visible
    const people = [...state.people].sort((a, b) => (a.band_id ? 0 : 1) - (b.band_id ? 0 : 1) || a.id - b.id);
    const layout = state.room && people.length ? state.room : null;
    room.classList.toggle("modeled", !!layout); // may change the frame height, so measure after
    const w = room.clientWidth, h = room.clientHeight;
    $("#roomEmpty").classList.toggle("hidden", people.length > 0);
    $("#roomTitle").innerHTML = people.length
      ? `<b>${esc(layout ? layout.name : "Live room")}</b><span>${people.length} people · ${state.matches.length} connections</span>` : "";

    // ---- geometry: board is an N×N diamond grid with a thick slab underneath
    const N = layout ? layout.grid : people.length > 8 ? 10 : 8;
    const small = w < 760;
    const padX = small ? 12 : 40, padTop = small ? 70 : layout ? 96 : 120, padBottom = small ? 50 : 56;
    const tw = Math.max(24, Math.min((w - 2 * padX) / N, (h - padTop - padBottom) / (N / 2 + 0.4)));
    const th = tw / 2, depth = Math.max(10, th * 0.7);
    const ox = w / 2, oy = padTop + Math.max(0, (h - padTop - padBottom - (N * th + depth)) / 2);
    const iso = (c, r) => ({ x: ox + ((c - r) * tw) / 2, y: oy + ((c + r) * th) / 2 });
    const down = (p) => ({ x: p.x, y: p.y + depth });
    const H = tw * (layout ? 0.7 : 0.95), orbR = Math.max(12, tw * (layout ? 0.27 : 0.23));
    const cells = placePeople(people, N, layout);
    const anchor = {}; // orb centre per person (where lines attach)
    const orbBoxes = [];
    geom = { iso, cells, tw, th };

    const el = $("#iso");
    el.setAttribute("viewBox", `0 0 ${w} ${h}`);

    // ---- board
    const board = $("#isoBoard"); board.innerHTML = "";
    const A = iso(0, 0), B = iso(N, 0), C = iso(N, N), D = iso(0, N);
    const mid = iso(N / 2, N / 2);
    svg("ellipse", { class: "mist", cx: mid.x, cy: mid.y + N * th * 0.42, rx: N * tw * 0.42, ry: N * th * 0.28 }, board);
    svg("polygon", { class: "face-l", points: pts([D, C, down(C), down(D)]) }, board);
    svg("polygon", { class: "face-r", points: pts([C, B, down(B), down(C)]) }, board);
    svg("polygon", { class: "top", points: pts([A, B, C, D]) }, board);
    const gap = 0.05;
    for (let c = 0; c < N; c++) for (let r = 0; r < N; r++) {
      svg("polygon", {
        class: `tile${(c + r) % 2 ? " alt" : ""}`,
        points: pts([iso(c + gap, r + gap), iso(c + 1 - gap, r + gap), iso(c + 1 - gap, r + 1 - gap), iso(c + gap, r + 1 - gap)]),
      }, board);
    }
    svg("polyline", { class: "rim rim-top", points: pts([D, A, B]) }, board);
    svg("line", { class: "rim rim-l", x1: D.x, y1: D.y, x2: C.x, y2: C.y }, board);
    svg("line", { class: "rim rim-r", x1: C.x, y1: C.y, x2: B.x, y2: B.y }, board);

    // ---- furniture + people, painted back to front so nearer things overlap farther ones
    const piecesG = $("#isoPieces"); piecesG.innerHTML = "";
    const labels = $("#labels"); labels.innerHTML = "";
    const drawables = [];
    for (const z of (layout && layout.zones) || []) drawables.push({ key: z.c1 + z.r1 - 0.5, zone: z });
    for (const p of people) drawables.push({ key: cells[p.id].c + cells[p.id].r, person: p });
    drawables.sort((a, b) => a.key - b.key);

    for (const d of drawables) {
      if (d.zone) { drawZone(d.zone, svg("g", { class: `zone ${d.zone.kind}` }, piecesG), iso, tw, th, H, labels); continue; }
      const p = d.person;
      const { c, r } = cells[p.id];
      const P = iso(c + 0.5, r + 0.5);
      const band = p.band_id ? state.bands.find((b) => b.id === p.band_id) : null;
      const g = svg("g", { class: "piece", "data-id": p.id }, piecesG);
      const i = 0.1;
      svg("polygon", { class: "pad", points: pts([iso(c + i, r + i), iso(c + 1 - i, r + i), iso(c + 1 - i, r + 1 - i), iso(c + i, r + 1 - i)]) }, g);
      svg("ellipse", { class: "ring", cx: P.x, cy: P.y, rx: tw * 0.26, ry: th * 0.26 }, g);
      svg("rect", { class: "beam", x: P.x - tw * 0.04, y: P.y - H, width: tw * 0.08, height: H, rx: tw * 0.04 }, g);
      const O = { x: P.x, y: P.y - H };
      anchor[p.id] = O;
      orbBoxes.push({ l: O.x - orbR, t: O.y - orbR, r: O.x + orbR, b: O.y + orbR });
      svg("circle", { class: "halo", cx: O.x, cy: O.y, r: orbR * 1.6 }, g);
      svg("circle", { class: "orb", cx: O.x, cy: O.y, r: orbR, fill: avatarColor(p.name) }, g);
      const t = svg("text", { class: "orb-text", x: O.x, y: O.y + orbR * 0.34, "text-anchor": "middle", "font-size": orbR * 0.9 }, g);
      t.textContent = initials(p.name);
      if (p.photo) {
        const cp = svg("clipPath", { id: `cp-${p.id}` }, g);
        svg("circle", { cx: O.x, cy: O.y, r: orbR }, cp);
        const im = svg("image", {
          href: p.photo, x: O.x - orbR, y: O.y - orbR, width: orbR * 2, height: orbR * 2,
          preserveAspectRatio: "xMidYMid slice", "clip-path": `url(#cp-${p.id})`,
        }, g);
        im.addEventListener("error", () => im.remove());
        svg("circle", { class: "orb-ring", cx: O.x, cy: O.y, r: orbR }, g);
      }
      if (band) {
        const bx = O.x + orbR * 0.75, by = O.y - orbR * 0.75, s = Math.max(9, orbR * 0.42);
        svg("circle", { class: "btag", cx: bx, cy: by, r: s }, g);
        const bt = svg("text", { class: "btag-text", x: bx, y: by + s * 0.38, "text-anchor": "middle", "font-size": s * 1.05 }, g);
        bt.textContent = band.id;
      }
      const lab = document.createElement("div");
      lab.className = `plabel${layout ? " compact" : ""}`;
      lab.style.left = `${P.x}px`;
      lab.style.top = `${P.y + th * 0.42}px`;
      lab.innerHTML = layout
        ? `<div class="nm">${esc(p.name)}</div><div class="rl">${esc(p.company || p.role || "")}</div>`
        : `<div class="nm">${esc(p.name)}</div><div class="rl">${esc([p.role, p.company].filter(Boolean).join(" · "))}</div>`;
      labels.appendChild(lab);
    }
    if (layout && !small) declutterLabels(orbBoxes);
    renderRoomStates();

    // ---- connections: arcs through the air between avatars
    const edges = $("#isoEdges"); edges.innerHTML = "";
    const firstDraw = seenMatches === null;
    if (firstDraw) seenMatches = new Set();
    const pairCount = {};
    state.matches.forEach((m) => {
      const a = anchor[m.a], b = anchor[m.b];
      if (!a || !b) return;
      const key = `${Math.min(m.a, m.b)}-${Math.max(m.a, m.b)}`;
      const k = (pairCount[key] = (pairCount[key] || 0) + 1) - 1;
      const dist = Math.hypot(b.x - a.x, b.y - a.y);
      const lift = Math.min(dist * 0.32, h * 0.22) + 18 + k * 30;
      const q = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - lift };
      const isNew = !firstDraw && !seenMatches.has(m.id);
      const path = svg("path", { class: `edge ${m.kind}${isNew ? " new" : ""}`, d: `M${a.x},${a.y} Q${q.x},${q.y} ${b.x},${b.y}` }, edges);
      const top = { x: 0.25 * a.x + 0.5 * q.x + 0.25 * b.x, y: 0.25 * a.y + 0.5 * q.y + 0.25 * b.y };
      const dot = svg("circle", { class: `edge-dot ${m.kind}`, cx: top.x, cy: top.y, r: 5 }, edges);
      const title = svg("title", {}, dot); title.textContent = m.reason;
      if (isNew) {
        const L = path.getTotalLength();
        if (m.kind === "handshake") {
          path.style.strokeDasharray = `${L}`; path.style.strokeDashoffset = `${L}`;
          path.getBoundingClientRect();
          path.style.transition = "stroke-dashoffset 1.2s cubic-bezier(.22,.8,.24,1)";
          requestAnimationFrame(() => { path.style.strokeDashoffset = "0"; });
        }
        ripple(m.a, m.kind); ripple(m.b, m.kind);
        setTimeout(() => path.classList.remove("new"), 5000);
      }
      seenMatches.add(m.id);
    });
  }

  function ripple(pid, kind) {
    if (!geom || !geom.cells[pid]) return;
    const { c, r } = geom.cells[pid];
    const { iso } = geom;
    const el = svg("polygon", { class: `ripple ${kind}`, points: pts([iso(c, r), iso(c + 1, r), iso(c + 1, r + 1), iso(c, r + 1)]) }, $("#isoBoard"));
    setTimeout(() => el.remove(), 2300);
  }

  function renderRoomStates() {
    $$("#isoPieces .piece").forEach((el) => {
      const p = state.people.find((x) => String(x.id) === el.dataset.id);
      const band = p && p.band_id ? state.bands.find((b) => b.id === p.band_id) : null;
      el.classList.remove("listening", "saved", "matched", "idle", "flash");
      if (band) el.classList.add(band.led);
      if (p && flashPeople.has(p.id)) el.classList.add("flash");
    });
  }

  // ============================================================ banner + toasts
  const bannerQueue = [];
  let bannerBusy = false;
  function banner(text, sub, kind) {
    bannerQueue.push({ text, sub, kind });
    if (!bannerBusy) nextBanner();
  }
  function nextBanner() {
    const b = bannerQueue.shift();
    const el = $("#roomBanner");
    if (!b) { bannerBusy = false; return; }
    bannerBusy = true;
    el.className = `room-banner ${b.kind}`;
    el.innerHTML = `${esc(b.text)}<small>${esc(b.sub)}</small>`;
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(nextBanner, 600); }, 4200);
  }

  function toast(title, sub = "", kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ""}`;
    $("#toasts").appendChild(el);
    while ($("#toasts").children.length > 3) $("#toasts").firstChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 420); }, kind === "err" ? 7000 : 5000);
  }

  // ============================================================ demo controls
  function bandOptions(selected) {
    return state.bands.map((b) => `<option value="${esc(b.id)}" ${b.id === selected ? "selected" : ""}>${esc(b.id)} · ${esc(bandName(b))}</option>`).join("");
  }

  function renderSelects() {
    const defaults = { tBand: "A", hA: "A", hB: "B", iSpeaker: "B", iListener: "A" };
    Object.entries(defaults).forEach(([id, def]) => {
      const el = $("#" + id);
      const cur = el.value || def;
      el.innerHTML = bandOptions(cur);
      if (!el.value && state.bands[0]) el.value = state.bands[0].id;
    });
  }

  async function withBusy(btn, fn) {
    btn.classList.add("busy");
    try { return await fn(); }
    catch (e) { toast("Something went wrong", e.message, "err"); }
    finally { btn.classList.remove("busy"); }
  }

  const loadSample = (e) => withBusy(e.currentTarget, async () => { state = await api("/api/demo/seed", { method: "POST" }); render(); });

  // ============================================================ profile photo dialog
  let photoFor = null;
  function openPhotoDialog(pid, name) {
    photoFor = pid;
    const p = state.people.find((x) => x.id === pid) || { name, photo: "" };
    $("#pdAvatar").innerHTML = avatar(p, { size: "lg" });
    $("#pdTitle").textContent = `Photo for ${first(p.name || name)}`;
    $("#pdUrl").value = "";
    $("#pdError").textContent = "";
    $("#pdRemove").classList.toggle("hidden", !(p.photo || "").startsWith("/api/"));
    $("#photoDialog").showModal();
  }
  async function savePhoto(body, btn) {
    btn.classList.add("busy");
    $("#pdError").textContent = "";
    try {
      await api(`/api/people/${photoFor}/photo`, { method: "PUT", body });
      $("#photoDialog").close();
      toast("Photo updated", "Shown everywhere this person appears", "ok");
    } catch (e) { $("#pdError").textContent = e.message; }
    finally { btn.classList.remove("busy"); }
  }
  /** Shrink uploads to 400px JPEG in the browser before sending. */
  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, 400 / Math.max(img.width, img.height));
        const cv = document.createElement("canvas");
        cv.width = Math.round(img.width * s); cv.height = Math.round(img.height * s);
        cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
        URL.revokeObjectURL(img.src);
        resolve(cv.toDataURL("image/jpeg", 0.88));
      };
      img.onerror = () => reject(new Error("That file isn't an image we can read."));
      img.src = URL.createObjectURL(file);
    });
  }
  function wirePhotoDialog() {
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-photo]");
      if (b) { e.preventDefault(); e.stopPropagation(); openPhotoDialog(Number(b.dataset.photo), b.dataset.name); }
    });
    $("#pdUseLink").addEventListener("click", (e) => {
      const url = $("#pdUrl").value.trim();
      if (!url) { $("#pdError").textContent = "Paste an image link first."; return; }
      savePhoto({ url }, e.currentTarget);
    });
    $("#pdUrl").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#pdUseLink").click(); } });
    $("#pdFile").addEventListener("change", async (e) => {
      const f = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!f) return;
      try { await savePhoto({ data_url: await fileToDataUrl(f) }, $(".pd-upload")); }
      catch (err) { $("#pdError").textContent = err.message; }
    });
    $("#pdRemove").addEventListener("click", async () => {
      try { await api(`/api/people/${photoFor}/photo`, { method: "DELETE" }); $("#photoDialog").close(); toast("Photo removed"); }
      catch (err) { $("#pdError").textContent = err.message; }
    });
    $("#pdCancel").addEventListener("click", () => $("#photoDialog").close());
  }

  function wire() {
    wirePhotoDialog();
    $$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
    $("#chatBands").addEventListener("click", (e) => {
      const b = e.target.closest("[data-band]");
      if (!b) return;
      chatBand = b.dataset.band; chatPerson = null;
      $("#chat").classList.remove("show-thread");
      renderChatBands();
      loadThreads().catch((err) => toast("Couldn't load conversations", err.message, "err"));
    });
    $("#chatList").addEventListener("click", (e) => {
      const it = e.target.closest("[data-person]");
      if (!it) return;
      chatPerson = Number(it.dataset.person);
      renderChatList();
      $("#chat").classList.add("show-thread");
      loadThread().catch((err) => toast("Couldn't open conversation", err.message, "err"));
    });
    $("#thread").addEventListener("click", async (e) => {
      if (e.target.closest("[data-back]")) { $("#chat").classList.remove("show-thread"); return; }
      const del = e.target.closest("[data-del]");
      if (!del || !confirm("Delete this recording? The contact card stays. This can't be undone.")) return;
      try { await api(`/api/conversations/${del.dataset.del}`, { method: "DELETE" }); toast("Recording deleted"); scheduleRefresh(); }
      catch (err) { toast("Couldn't delete", err.message, "err"); }
    });
    $("#filters").addEventListener("click", (e) => {
      const c = e.target.closest("[data-filter]");
      if (c) { filter = c.dataset.filter; renderFilters(); renderCards(); }
    });

    const drawer = $("#drawer"), scrim = $("#scrim");
    const setDrawer = (open) => {
      drawer.classList.toggle("open", open); scrim.classList.toggle("open", open);
      drawer.setAttribute("aria-hidden", String(!open)); $("#drawerBtn").setAttribute("aria-expanded", String(open));
    };
    $("#drawerBtn").addEventListener("click", () => setDrawer(!drawer.classList.contains("open")));
    $("#drawerClose").addEventListener("click", () => setDrawer(false));
    scrim.addEventListener("click", () => setDrawer(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { setDrawer(false); setProjector(false); } });

    $("#seedBtn").addEventListener("click", loadSample);
    $("#emptySeedBtn").addEventListener("click", loadSample);
    $("#resetBtn").addEventListener("click", (e) => {
      if (!confirm("Clear all contacts, people and matches? This can't be undone.")) return;
      withBusy(e.currentTarget, async () => { state = await api("/api/session/reset", { method: "POST" }); render(); });
    });
    $("#projectorBtn").addEventListener("click", () => setProjector(true, false, true));
    $("#exitProjector").addEventListener("click", () => setProjector(false));
    document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement) setProjector(false, true); });
    window.addEventListener("resize", () => { clearTimeout(window.__rt); window.__rt = setTimeout(renderRoom, 120); });

    // 1. conversation
    $("#tPreset").innerHTML += presets.transcripts.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
    $("#tPreset").addEventListener("change", (e) => {
      const p = presets.transcripts.find((x) => x.id === e.target.value);
      if (p) { $("#tText").value = p.text; $("#tBand").value = p.band; }
    });
    $("#tSend").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
      const text = $("#tText").value.trim();
      if (text.length < 10) throw new Error("Pick a conversation or write one first.");
      const band = $("#tBand").value;
      const r = await api(`/api/bands/${band}/transcript`, { method: "POST", body: { text } });
      if (state.bands.find((b) => b.id === band && b.owner)) { chatBand = band; chatPerson = r.contact.person_id; }
      showView("chats");
    }));

    // 2. handshake (two gestures 0.3 s apart; the server pairs anything within 1.5 s)
    $("#hSend").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
      const a = $("#hA").value, b = $("#hB").value;
      if (a === b) throw new Error("Pick two different bands.");
      await api(`/api/bands/${a}/gesture`, { method: "POST", body: { type: "handshake", timestamp: Date.now() } });
      await new Promise((r) => setTimeout(r, 300));
      await api(`/api/bands/${b}/gesture`, { method: "POST", body: { type: "handshake", timestamp: Date.now() } });
    }));

    // 3. spoken intro
    $("#iPreset").innerHTML += presets.intros.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
    $("#iPreset").addEventListener("change", (e) => {
      const p = presets.intros.find((x) => x.id === e.target.value);
      if (p) { $("#iText").value = p.utterance; $("#iSpeaker").value = p.speaker_band_id; $("#iListener").value = p.listener_band_id; }
    });
    $("#iSend").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
      const utterance = $("#iText").value.trim();
      if (!utterance) throw new Error("Pick an example or write something like “You should meet Sam”.");
      const r = await api("/api/room/match", { method: "POST", body: { utterance, speaker_band_id: $("#iSpeaker").value, listener_band_id: $("#iListener").value } });
      if (!r.matches.length) toast("No intro heard", r.message || "", "err"); else showView("room");
    }));

    // 4. matches
    $("#mSend").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
      const r = await api("/api/room/match", { method: "POST", body: {} });
      if (!r.matches.length) toast("No new matches", "Everyone whose needs fit is already connected.");
      else showView("room");
    }));
  }

  function showView(v) {
    $$(".tab").forEach((t) => { const on = t.dataset.view === v; t.classList.toggle("active", on); t.setAttribute("aria-selected", String(on)); });
    $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
    document.body.classList.toggle("on-room", v === "room");
    const [t, sub] = VIEW_TITLE[v] || VIEW_TITLE.chats;
    $("#viewTitle").innerHTML = `<span>${esc(t)}</span> <span class="gray">${esc(sub)}</span>`;
    if (v === "room") requestAnimationFrame(renderRoom);
  }

  function setProjector(on, fromFsEvent = false, wantFullscreen = false) {
    document.body.classList.toggle("projector", on);
    if (on) {
      showView("room");
      // Browsers only allow fullscreen from a click, so ?projector just hides everything else.
      if (wantFullscreen && document.documentElement.requestFullscreen && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
    } else if (!fromFsEvent && document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
    requestAnimationFrame(renderRoom);
  }

  // ============================================================ boot
  async function boot() {
    try { presets = await api("/api/presets"); } catch (e) { console.warn(e); }
    wire();
    await refresh();
    connect();
    if (new URLSearchParams(location.search).has("projector")) setProjector(true);
  }
  boot().catch((e) => { console.error(e); toast("Couldn't reach the server", e.message, "err"); });
})();
