/* Hackathon Screening dashboard — vanilla JS, no build step. */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const TRACK_LABELS = {
  interested: "Tertarik", registered: "Terdaftar", building: "Dikerjakan",
  submitted: "Submitted", won: "Menang", lost: "Kalah",
};
const BOARD_COLUMNS = Object.keys(TRACK_LABELS);
const PAGES = {
  overview: ["Ringkasan", "Gambaran singkat lomba yang kamu jalani"],
  board: ["Papan Saya", "Kanban lomba yang sedang kamu ikuti"],
  agenda: ["Agenda", "Deadline resmi dan target pribadimu"],
  recommend: ["Rekomendasi", "Diurutkan dari kecocokan dengan lomba yang kamu lacak"],
  digest: ["Digest harian", "Top 5 minggu ini, disusun otomatis tiap pagi"],
  discover: ["Jelajahi", "Semua lomba hasil scraping, tersimpan lokal"],
  scraper: ["Scraper", "Atur filter, jalankan, dan lihat riwayat scraping"],
};

const AUDIENCE_LABELS = { student: "Mahasiswa/pelajar", general: "Umum" };
const ELIG_LABELS = {
  eligible: `<svg class="i i-xs" aria-hidden="true"><use href="#i-check-circle"/></svg> bisa ikut`,
  check: `<svg class="i i-xs" aria-hidden="true"><use href="#i-alert"/></svg> perlu dicek`,
  blocked: `<svg class="i i-xs" aria-hidden="true"><use href="#i-x"/></svg> tidak bisa`,
};
const LEVEL_LABELS = {
  none: "bukan pelajar", recent_grad: "baru lulus / non-gelar", highschool: "siswa SMA",
  undergrad: "mahasiswa S1", postgrad: "mahasiswa S2/S3",
};
const TRAVEL_LABELS = {
  online_only: "online saja", domestic: "dalam negeri", international: "bisa ke luar negeri",
};

async function setFeedback(id, kind) {
  const current = state.feedback[id];
  if (current === kind) {
    await api(`/api/feedback/${encodeURIComponent(id)}`, { method: "DELETE" });
    delete state.feedback[id];
    toast(kind === "like" ? "Suka dibatalkan" : "Kembali direkomendasikan");
  } else {
    await send(`/api/feedback/${encodeURIComponent(id)}`, "PUT", { kind });
    state.feedback[id] = kind;
    toast(kind === "like" ? "Disematkan di atas" : "Tidak akan direkomendasikan lagi");
  }
}

function fbButtons(h) {
  const fb = h.feedback;
  return `<button class="btn sm fb-btn ${fb === "like" ? "on-like" : ""}" data-fb="like" data-target="${esc(h.id)}"
            title="Sematkan di urutan atas" aria-label="Sematkan di urutan atas"><svg class="i i-sm" aria-hidden="true"><use href="#i-heart"/></svg></button>
          <button class="btn sm fb-btn ${fb === "dismiss" ? "on-dismiss" : ""}" data-fb="dismiss" data-target="${esc(h.id)}"
            title="Jangan rekomendasikan lagi" aria-label="Jangan rekomendasikan lagi"><svg class="i i-sm" aria-hidden="true"><use href="#i-x"/></svg></button>`;
}

function bindFeedback(root, after) {
  $$(`${root} [data-fb]`).forEach((btn) =>
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await setFeedback(btn.dataset.target, btn.dataset.fb);
      after();
    }));
}

const eligChip = (e) => e ? `<span class="elig ${e.verdict}">${ELIG_LABELS[e.verdict]}</span>` : "";
const eligWhy = (e) => {
  if (!e) return "";
  const lines = [...e.blockers.map((b) => `<span class="b">${esc(b)}</span>`),
                 ...e.warnings.map((w) => `<span class="w">${esc(w)}</span>`)];
  return lines.length ? `<div class="elig-why">${lines.join("")}</div>` : "";
};
const LEVEL_ICONS = {
  overdue: "alert", critical: "alert", warning: "alert", soon: "agenda", info: "info",
};

const state = {
  facets: null, tracked: [], current: null, checklist: [], page: "overview",
  notifications: { items: [], urgent: 0 },
  feedback: {},
};

/* Desktop popups are opt-in and fire at most once per hackathon per day. */
const POPPED_KEY = "hs-popped";
const popped = () => { try { return JSON.parse(localStorage.getItem(POPPED_KEY) || "{}"); } catch { return {}; } };
const markPopped = (map) => { try { localStorage.setItem(POPPED_KEY, JSON.stringify(map)); } catch { /* private mode */ } };

/* ---------------------------------------------------------------- helpers */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (m) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

const money = (n, c) => (n == null ? "—" : (c === "EUR" ? "€" : "$") + n.toLocaleString("en-US"));

/* Prize totals arrive split by currency — there is no rate to merge them with,
   so show each one rather than stamping the sum with a currency it is not. */
const prizePool = (stats) => {
  const by = stats.active_prize_by_currency || {};
  const parts = Object.entries(by).filter(([, v]) => v > 0);
  if (!parts.length) return "—";
  return parts.map(([cur, amount]) => money(amount, cur)).join(" + ");
};

function countdown(endAt) {
  if (!endAt) return { text: "tanpa tanggal", cls: "due-none" };
  const ms = new Date(endAt) - new Date();
  if (ms <= 0) return { text: "selesai", cls: "due-none" };
  const h = ms / 36e5, d = Math.floor(h / 24);
  if (h < 48) return { text: h < 1 ? "<1 jam lagi" : `${Math.floor(h)} jam lagi`, cls: "due-soon" };
  if (d <= 7) return { text: `${d} hari lagi`, cls: "due-soon" };
  if (d <= 21) return { text: `${d} hari lagi`, cls: "due-warn" };
  return { text: `${d} hari lagi`, cls: "due-ok" };
}

const when = (iso) => (iso ? new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }) : "—");
const ago = (iso) => {
  if (!iso) return "belum pernah";
  const m = (Date.now() - new Date(iso)) / 6e4;
  if (m < 1) return "baru saja";
  if (m < 60) return `${Math.floor(m)} menit lalu`;
  if (m < 1440) return `${Math.floor(m / 60)} jam lalu`;
  return `${Math.floor(m / 1440)} hari lalu`;
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ? JSON.stringify(body.detail) : res.statusText);
  }
  return res.json();
}
const send = (path, method, body) =>
  api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2200);
}
function skeleton(sel, kind, n = 3) {
  const el = $(sel);
  if (!el || el.dataset.painted) return;
  el.innerHTML = `<div class="skel skel-${kind}"></div>`.repeat(n);
}
function painted(sel) { const el = $(sel); if (el) el.dataset.painted = "1"; }

const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ---------------------------------------------------------------- routing */

function go(page) {
  state.page = page;
  $$("nav a").forEach((a) => {
    const on = a.dataset.page === page;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $$("section[data-view]").forEach((s) => (s.hidden = s.dataset.view !== page));
  const [title, sub] = PAGES[page];
  $("#page-title").textContent = title;
  $("#page-sub").textContent = sub;
  ({ overview: loadOverview, board: loadBoard, agenda: loadAgenda,
     discover: loadDiscover, recommend: loadRecommend, digest: loadDigest,
     scraper: loadScraper }[page])();
}
$$("nav a").forEach((a) => {
  a.addEventListener("click", () => go(a.dataset.page));
  // these are buttons in behaviour; without this they are mouse-only
  a.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(a.dataset.page); }
  });
});

/* ---------------------------------------------------------------- chrome */

async function loadChrome() {
  const [stats, sources] = await Promise.all([api("/api/stats"), api("/api/sources")]);
  $("#nav-tracked").textContent = stats.tracked_total;
  $("#nav-total").textContent = stats.total;
  $("#nav-agenda").textContent = stats.deadlines_this_week;
  const newest = sources.items.map((s) => s.last_success).filter(Boolean).sort().pop();
  $("#foot-sync").textContent = "Sinkron " + ago(newest);
  $("#foot-db").textContent = `${stats.total} lomba tersimpan · ${stats.by_status.open || 0} berjalan`;
  await loadNotifications();
  await loadProfileSummary();
  return stats;
}

/* ---------------------------------------------------------------- profile */

let profileState = null;

async function loadProfileSummary() {
  const data = await api("/api/profile");
  profileState = data;
  const p = data.profile;
  $("#foot-profile").textContent = `${p.country} · ${LEVEL_LABELS[p.student_level]} · ${TRAVEL_LABELS[p.travel]}`;
  return data;
}

function openProfile() {
  const p = profileState.profile;
  $("#p-country").value = p.country;
  $("#p-level").value = p.student_level;
  $("#p-travel").value = p.travel;
  $("#p-invite").checked = p.allow_invite_only;
  renderImpact(profileState.impact);
  $("#profile-dlg").showModal();
}

function renderImpact(impact) {
  const total = impact.eligible + impact.check + impact.blocked;
  $("#p-impact").innerHTML = `<div class="k" style="color:var(--faint);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase">Dampak ke ${total} lomba yang masih hidup</div>
    <div class="meta" style="margin-top:6px;font-size:12.5px">
      <span class="elig eligible">${impact.eligible} bisa ikut</span>
      <span class="elig check">${impact.check} perlu dicek</span>
      <span class="elig blocked">${impact.blocked} tersaring</span>
    </div>`;
}

$("#open-profile").addEventListener("click", openProfile);
$("#p-cancel").addEventListener("click", () => $("#profile-dlg").close());
$("#p-save").addEventListener("click", async () => {
  const level = $("#p-level").value;
  await send("/api/profile", "PUT", {
    country: ($("#p-country").value || "ID").toUpperCase().slice(0, 2),
    student_level: level,
    travel: $("#p-travel").value,
    allow_invite_only: $("#p-invite").checked,
  });
  $("#profile-dlg").close();
  toast("Profil disimpan — daftar ikut menyesuaikan");
  await loadProfileSummary();
  go(state.page);
});

/* live preview of the impact while the dialog is open */
["p-country", "p-level", "p-travel", "p-invite"].forEach((id) =>
  document.getElementById(id).addEventListener("change", async () => {
    const level = $("#p-level").value;
    await send("/api/profile", "PUT", {
      country: ($("#p-country").value || "ID").toUpperCase().slice(0, 2),
      student_level: level,
      travel: $("#p-travel").value,
      allow_invite_only: $("#p-invite").checked,
    });
    renderImpact((await loadProfileSummary()).impact);
  }));

/* ---------------------------------------------------------------- notifications */

async function loadNotifications() {
  const data = await api("/api/notifications");
  state.notifications = data;

  const badge = $("#bell-badge");
  badge.hidden = data.urgent === 0;
  badge.textContent = data.urgent;
  $("#bell").classList.toggle("due-soon", data.urgent > 0);

  $("#notif-list").innerHTML = data.items.length
    ? data.items.map((n) => `<div class="notif ${n.level}" data-id="${esc(n.id)}">
        <span class="lead"><svg class="i i-sm" aria-hidden="true"><use href="#i-${LEVEL_ICONS[n.level] || "info"}"/></svg></span>
        <div class="grow">
          <h6>${esc(n.title)}</h6>
          <div class="meta">
            <span class="${n.level === "soon" || n.level === "info" ? "due-warn" : "due-soon"}">${esc(n.message)}</span>
            <span class="tag ${n.source}">${n.source}</span>
            ${n.progress ? `<span>${n.progress}%</span>` : ""}
          </div>
        </div>
      </div>`).join("")
    : `<div class="empty" style="border:0;padding:22px"><span style="display:inline-flex;align-items:center;gap:7px;color:var(--good)"><svg class="i" aria-hidden="true"><use href="#i-check-circle"/></svg> Tidak ada deadline mendesak.</span></div>`;
  bindCards("#notif-list");

  $("#notif-foot").textContent = [
    data.count
      ? `${data.count} lomba punya deadline dalam 14 hari — ${data.urgent} mendesak.`
      : "Lacak lomba dulu supaya deadline-nya dipantau di sini.",
    popupState(),
  ].filter(Boolean).join(" ");

  renderBanner(data);
  maybePopup(data);
}

function renderBanner(data) {
  const hot = data.items.filter((n) => n.level === "overdue" || n.level === "critical");
  const warm = data.items.filter((n) => n.level === "warning");
  const box = $("#alert-banner");
  if (!hot.length && !warm.length) { box.innerHTML = ""; return; }
  const list = hot.length ? hot : warm;
  box.innerHTML = `<div class="banner ${hot.length ? "" : "warn"}">
    <svg class="i" aria-hidden="true" style="width:19px;height:19px"><use href="#i-alert"/></svg>
    <div class="grow">
      <b>${list.length} lomba butuh perhatian sekarang</b>
      <div class="meta">${list.slice(0, 3).map((n) => `${esc(n.title)} — ${esc(n.message.toLowerCase())}`).join(" · ")}</div>
    </div>
    <button class="btn sm" id="banner-go">lihat agenda</button>
  </div>`;
  $("#banner-go").addEventListener("click", () => go("agenda"));
}

function popupState() {
  if (!("Notification" in window)) return "Popup desktop tidak didukung browser ini.";
  return {
    granted: "Popup desktop aktif.",
    denied: "Popup desktop diblokir browser — izinkan lewat setelan situs kalau mau.",
    default: "",
  }[Notification.permission];
}

/* Desktop notification: only for overdue/critical, once per day per hackathon. */
function maybePopup(data) {
  const btn = $("#notif-permission");
  const supported = "Notification" in window;
  btn.hidden = !supported || Notification.permission !== "default";
  if (!supported || Notification.permission !== "granted") return;

  const today = new Date().toISOString().slice(0, 10);
  const seen = popped();
  let changed = false;
  data.items
    .filter((n) => n.level === "overdue" || n.level === "critical")
    .forEach((n) => {
      if (seen[n.id] === today) return;
      new Notification(n.title, { body: n.message, tag: n.id });
      seen[n.id] = today;
      changed = true;
    });
  if (changed) markPopped(seen);
}

function setNotifOpen(open) {
  $("#notif-pop").hidden = !open;
  $("#bell").setAttribute("aria-expanded", String(open));
}
$("#bell").addEventListener("click", (e) => {
  e.stopPropagation();
  setNotifOpen($("#notif-pop").hidden);
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".bell-wrap")) setNotifOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#notif-pop").hidden) { setNotifOpen(false); $("#bell").focus(); }
});
$("#notif-permission").addEventListener("click", async (e) => {
  e.stopPropagation();
  const result = await Notification.requestPermission();
  toast(result === "granted" ? "Popup deadline aktif" : "Popup ditolak browser");
  loadNotifications();
});

/* ---------------------------------------------------------------- overview */

async function loadOverview() {
  skeleton("#stats", "stat", 6);
  skeleton("#agenda-mini", "row", 4);
  skeleton("#activity", "row", 4);
  skeleton("#reco-mini", "card", 3);
  const [stats, agenda, act, sources] = await Promise.all([
    loadChrome(), api("/api/agenda?days=30"), api("/api/activity?limit=8"), api("/api/sources"),
  ]);

  $("#stats").innerHTML = [
    ["Dilacak", stats.tracked_total, "lomba di papanmu"],
    ["Deadline ≤7 hari", stats.deadlines_this_week, "belum submit"],
    ["Prize pool aktif", prizePool(stats), "yang masih diperebutkan"],
    ["Sudah submit", stats.submitted_total, `win rate ${stats.win_rate}%`],
    ["Sedang berjalan", stats.by_status.open || 0, "dari semua sumber"],
    ["Total tersimpan", stats.total, "di database lokal"],
  ].map(([k, v, h]) => `<div class="stat"><small>${k}</small><b>${v}</b><div class="hint">${h}</div></div>`).join("");

  $("#agenda-mini").innerHTML = agenda.items.length
    ? agenda.items.slice(0, 6).map(agendaRow).join("")
    : `<div class="empty">Belum ada deadline yang dipantau.
        <button class="btn sm" data-goto="discover">Jelajahi lomba</button></div>`;
  bindCards("#agenda-mini");
  bindGoto("#agenda-mini");

  $("#activity").innerHTML = act.items.length
    ? act.items.map((e) => `<div class="tl"><span class="when">${ago(e.created_at)}</span>
        <span><b>${esc(e.title)}</b> — ${esc(e.message)}</span></div>`).join("")
    : `<div class="empty">Belum ada aktivitas.</div>`;

  $("#source-cards").innerHTML = sources.items.map(sourceCard).join("");

  const reco = await api("/api/recommendations?limit=3");
  $("#reco-basis").textContent = recoBasis(reco.profile);
  $("#reco-mini").innerHTML = reco.items.map(recoCard).join("")
    || `<div class="empty">Belum ada kandidat di database.
        <button class="btn sm" data-goto="scraper">Jalankan scraping</button></div>`;
  bindCards("#reco-mini");
  bindGoto("#reco-mini");
  bindFeedback("#reco-mini", loadOverview);
}

$("#reco-more").addEventListener("click", () => go("recommend"));

/* ---------------------------------------------------------------- recommendations */

function recoBasis(profile) {
  if (profile.cold_start) return "belum ada riwayat — diurutkan dari sinyal umum";
  const bits = [`dari ${profile.sample_size} lomba di papanmu`];
  if (profile.top_themes.length) bits.push(profile.top_themes.slice(0, 3).join(", "));
  return bits.join(" · ");
}

function recoCard(h) {
  const c = countdown(h.end_at);
  const cls = h.score >= 85 ? "hot" : h.score >= 65 ? "mid" : "";
  state.feedback[h.id] = h.feedback || undefined;
  return `<div class="card reco ${h.feedback === "like" ? "pinned" : ""}" data-id="${esc(h.id)}">
    ${h.feedback === "like" ? `<div class="pin-flag"><svg class="i i-xs" aria-hidden="true"><use href="#i-pin"/></svg> disematkan</div>` : ""}
    <div class="top">
      <span class="score ${cls}">${h.score}</span>
      <h4>${esc(h.title)}</h4>
    </div>
    <div class="reasons">${h.reasons.map((r) => `<span>${esc(r)}</span>`).join("")}</div>
    ${eligWhy(h.eligibility)}
    <div class="foot">
      ${eligChip(h.eligibility)}
      <span class="tag ${h.source}">${h.source}</span>
      <span class="tag">${h.audience === "student" ? "mahasiswa" : "umum"}</span>
      <span class="due ${c.cls}">${c.text}</span>
      <span class="acts">
        ${fbButtons(h)}
        <button class="btn sm" data-id="${esc(h.id)}">+ Lacak</button>
      </span>
    </div>
  </div>`;
}

const recoSources = new Set();

async function loadRecommend() {
  skeleton("#reco-list", "card", 6);
  const facets = await loadFacets();
  if (!$("#r-audience").dataset.filled) {
    $("#r-audience").innerHTML = '<option value="">Semua peserta</option>' +
      (facets.audiences || []).map((a) =>
        `<option value="${a.name}">${AUDIENCE_LABELS[a.name] || a.name}</option>`).join("");
    $("#r-mode").innerHTML = '<option value="">Semua format</option>' +
      facets.modes.map((m) => `<option value="${m.name}">${m.name}</option>`).join("");
    $("#r-sources").innerHTML = facets.sources
      .map((s) => `<span class="tag chip ${s}" role="checkbox" tabindex="0" aria-checked="false" data-source="${s}">${s}</span>`).join("");
    $$("#r-sources .chip").forEach((c) => c.addEventListener("click", () => {
      const s = c.dataset.source;
      recoSources.has(s) ? recoSources.delete(s) : recoSources.add(s);
      c.classList.toggle("on", recoSources.has(s));
      loadRecommend();
    }));
    $("#r-audience").dataset.filled = "1";
  }

  const p = new URLSearchParams({ limit: "24" });
  if ($("#r-audience").value) p.set("audience", $("#r-audience").value);
  if ($("#r-mode").value) p.set("mode", $("#r-mode").value);
  if ($("#r-days").value) p.set("max_days", $("#r-days").value);
  if (recoSources.size) p.set("source", [...recoSources].join(","));
  if ($("#r-dismissed").checked) p.set("include_dismissed", "true");

  const [data, rulesStat] = await Promise.all([
    api("/api/recommendations?" + p), api("/api/rules/status"),
  ]);
  const pending = rulesStat.totals.pending;
  $("#reco-warning").innerHTML = pending
    ? `<div class="banner warn"><svg class="i" aria-hidden="true" style="width:19px;height:19px"><use href="#i-alert"/></svg><div class="grow">
         <b>${pending} lomba aturannya belum diperiksa</b>
         <div class="meta">Larangan negara bisa saja ada tapi belum ketahuan. Biasanya terisi
         otomatis saat scraping — jalankan manual dari halaman Scraper kalau menumpuk.</div>
       </div><button class="btn sm" id="reco-fix">buka Scraper</button></div>`
    : "";
  if (pending) $("#reco-fix").addEventListener("click", () => go("scraper"));
  const pr = data.profile;
  $("#reco-profile").innerHTML = pr.cold_start
    ? `<div class="profile-box"><div>
         <div class="k">Mode</div><div class="v">Cold start — papanmu masih kosong</div></div>
       <div style="color:var(--muted);font-size:12.5px;flex:1">
         Peringkat sementara memakai sinyal umum: waktu persiapan, besar hadiah, dan jumlah pendaftar.
         Begitu kamu melacak beberapa lomba, urutannya menyesuaikan seleramu.</div></div>`
    : `<div class="profile-box">
         <div><div class="k">Dasar profil</div><div class="v">${pr.sample_size} lomba di papan</div></div>
         <div><div class="k">Tema favorit</div><div class="v">${pr.top_themes.slice(0, 4).map(esc).join(", ") || "—"}</div></div>
         <div><div class="k">Segmen</div><div class="v">${AUDIENCE_LABELS[pr.audience] || "—"}</div></div>
         <div><div class="k">Format</div><div class="v">${pr.mode || "—"}</div></div>
         <div><div class="k">Hadiah tengah</div><div class="v">${pr.median_prize ? money(pr.median_prize, "USD") : "—"}</div></div>
       </div>`;

  const up = data.user_profile;
  const notes = [];
  if (data.excluded_ineligible) {
    notes.push(`${data.excluded_ineligible} disembunyikan karena tidak memenuhi syaratmu `
      + `(${up.country}, ${LEVEL_LABELS[up.student_level]}, ${TRAVEL_LABELS[up.travel]})`);
  }
  if (data.excluded_dismissed && !$("#r-dismissed").checked) {
    notes.push(`${data.excluded_dismissed} ditandai tidak diminati`);
  }
  if (data.pinned) notes.push(`${data.pinned} disematkan di atas`);
  $("#reco-excluded").textContent = notes.length
    ? notes.join(" · ") + "."
    : `Semua ${data.candidates} kandidat lolos syaratmu.`;

  $("#reco-list").innerHTML = data.items.map(recoCard).join("")
    || `<div class="empty">Tidak ada kandidat yang cocok dengan filter ini.</div>`;
  bindCards("#reco-list");
  bindFeedback("#reco-list", loadRecommend);
}

["r-audience", "r-mode", "r-days", "r-dismissed"].forEach((id) =>
  document.getElementById(id).addEventListener("change", loadRecommend));
$("#r-reset").addEventListener("click", () => {
  ["r-audience", "r-mode", "r-days"].forEach((id) => (document.getElementById(id).value = ""));
  $("#r-dismissed").checked = false;
  recoSources.clear();
  $$("#r-sources .chip").forEach((c) => c.classList.remove("on"));
  loadRecommend();
});

function agendaRow(h) {
  const target = h.track_my_deadline || h.end_at;
  const c = countdown(target);
  const pct = h.track_progress || 0;
  return `<div class="rowitem" data-id="${esc(h.id)}" style="cursor:pointer">
    <div class="grow">
      <h5>${esc(h.title)}</h5>
      <div class="meta">
        <span class="tag ${h.source}">${h.source}</span>
        <span class="${c.cls}">${c.text}</span>
        <span>${when(target)}${h.track_my_deadline ? " · target pribadi" : ""}</span>
        <span>${TRACK_LABELS[h.track_status] || ""}</span>
      </div>
      <div class="bar" style="margin-top:7px"><i style="width:${pct}%"></i></div>
    </div>
  </div>`;
}

function sourceCard(s) {
  const stale = !s.last_success || (Date.now() - new Date(s.last_success)) / 6e4 > 360;
  return `<div class="card pad">
    <div class="meta"><span class="tag ${s.source}">${s.source}</span>
      <span class="${stale ? "due-warn" : "due-ok"}">${stale ? "perlu disegarkan" : "masih segar"}</span></div>
    <b style="display:block;font-size:19px;margin-top:6px">${s.stored}</b>
    <div class="hint" style="color:var(--faint);font-size:11.5px">${s.live} masih berjalan · sinkron ${ago(s.last_success)}</div>
  </div>`;
}

/* ---------------------------------------------------------------- board */

async function loadBoard() {
  const { items } = await api("/api/tracked");
  state.tracked = items;
  renderBoard();
}

function renderBoard() {
  const q = $("#board-q").value.trim().toLowerCase();
  const prio = $("#board-priority").value;
  const items = state.tracked.filter((h) =>
    (!q || h.title.toLowerCase().includes(q) || (h.track_project_name || "").toLowerCase().includes(q)) &&
    (!prio || String(h.track_priority) === prio));

  if (!state.tracked.length) {
    $("#board").innerHTML = `<div class="empty board-empty">Papan masih kosong.
      Mulai dari peringkat, lalu lacak yang kamu pilih.
      <button class="btn sm" data-goto="recommend">Lihat rekomendasi</button></div>`;
    bindGoto("#board");
    return;
  }
  $("#board").innerHTML = BOARD_COLUMNS.map((key) => {
    const mine = items.filter((h) => h.track_status === key);
    return `<div class="col"><h3>${TRACK_LABELS[key]}<span>${mine.length}</span></h3>
      ${mine.map(boardCard).join("") || '<div class="empty-col">kosong</div>'}</div>`;
  }).join("");
  bindCards("#board");
}

function boardCard(h) {
  const c = countdown(h.track_my_deadline || h.end_at);
  const list = h.track_checklist || [];
  const done = list.filter((i) => i.done).length;
  const pct = h.track_progress || 0;
  return `<div class="tcard p${h.track_priority || 2}" data-id="${esc(h.id)}">
    <div class="card-head">
      <span class="dot-level" aria-hidden="true"></span>
      <h4>${esc(h.title)}</h4>
    </div>
    <div class="meta">
      <span class="tag ${h.source}">${h.source}</span>
      <span class="${c.cls}">${c.text}</span>
    </div>
    ${pct ? `<div class="bar" style="margin-top:8px"><i style="width:${pct}%"></i></div>` : ""}
    <div class="meta" style="margin-top:7px">
      ${h.track_project_name ? `<span><svg class="i i-xs" aria-hidden="true"><use href="#i-package"/></svg> ${esc(h.track_project_name)}</span>` : ""}
      ${list.length ? `<span><svg class="i i-xs" aria-hidden="true"><use href="#i-checklist"/></svg> ${done}/${list.length}</span>` : ""}
      ${pct ? `<span>${pct}%</span>` : ""}
    </div>
  </div>`;
}

function bindChips(root, onToggle) {
  $$(`${root} .chip`).forEach((c) => {
    const flip = () => {
      const on = c.classList.toggle("on");
      if (c.hasAttribute("aria-checked")) c.setAttribute("aria-checked", String(on));
      if (onToggle) onToggle(c, on);
    };
    c.addEventListener("click", flip);
  });
}
document.addEventListener("keydown", (e) => {
  const chip = e.target.closest && e.target.closest(".chip");
  if (chip && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); chip.click(); }
});
// every chip group flips its own class; keep the announced state honest
document.addEventListener("click", (e) => {
  const chip = e.target.closest && e.target.closest(".chip[aria-checked]");
  if (chip) chip.setAttribute("aria-checked", String(chip.classList.contains("on")));
});
bindChips("#s-devpost-status");

function bindGoto(root) {
  $$(`${root} [data-goto]`).forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); go(el.dataset.goto); }));
}

function bindCards(root) {
  $$(`${root} [data-id]`).forEach((el) =>
    el.addEventListener("click", () => openEditor(el.dataset.id)));
}

$("#board-q").addEventListener("input", debounce(renderBoard, 150));
$("#board-priority").addEventListener("change", renderBoard);

/* ---------------------------------------------------------------- agenda */

async function loadAgenda() {
  const days = $("#agenda-days").value;
  const { items } = await api(`/api/agenda?days=${days}`);
  $("#agenda-full").innerHTML = items.length
    ? items.map(agendaRow).join("")
    : `<div class="empty">Tidak ada deadline dalam ${days} hari ke depan.</div>`;
  bindCards("#agenda-full");
}
$("#agenda-days").addEventListener("change", loadAgenda);

/* ---------------------------------------------------------------- discover */

const discoverSources = new Set();

async function loadFacets() {
  if (state.facets) return state.facets;
  state.facets = await api("/api/facets");
  $("#f-sources").innerHTML = state.facets.sources
    .map((s) => `<span class="tag chip ${s}" role="checkbox" tabindex="0" aria-checked="false" data-source="${s}">${s}</span>`).join("");
  $$("#f-sources .chip").forEach((c) => c.addEventListener("click", () => {
    const s = c.dataset.source;
    discoverSources.has(s) ? discoverSources.delete(s) : discoverSources.add(s);
    c.classList.toggle("on", discoverSources.has(s));
    loadDiscover();
  }));
  $("#f-audience").innerHTML = '<option value="">Semua peserta</option>' +
    (state.facets.audiences || []).map((a) =>
      `<option value="${a.name}">${AUDIENCE_LABELS[a.name] || a.name} (${a.count})</option>`).join("");
  $("#f-mode").innerHTML = '<option value="">Semua format</option>' +
    state.facets.modes.map((m) => `<option value="${m.name}">${m.name} (${m.count})</option>`).join("");
  $("#f-theme").innerHTML = '<option value="">Semua tema</option>' +
    state.facets.themes.map((t) => `<option value="${esc(t.name)}">${esc(t.name)} (${t.count})</option>`).join("");
  $("#s-sources").innerHTML = state.facets.sources
    .map((s) => `<span class="tag chip on ${s}" role="checkbox" tabindex="0" aria-checked="true" data-source="${s}">${s}</span>`).join("");
  $$("#s-sources .chip").forEach((c) =>
    c.addEventListener("click", () => c.classList.toggle("on")));
  return state.facets;
}

async function loadDiscover() {
  skeleton("#rows", "table", 8);
  await loadFacets();
  const p = new URLSearchParams();
  const q = $("#f-q").value.trim();
  if (q) p.set("q", q);
  if (discoverSources.size) p.set("source", [...discoverSources].join(","));
  if ($("#f-status").value) p.set("status", $("#f-status").value);
  if ($("#f-eligibility").value) p.set("eligibility", $("#f-eligibility").value);
  if ($("#f-audience").value) p.set("audience", $("#f-audience").value);
  if ($("#f-mode").value) p.set("mode", $("#f-mode").value);
  if ($("#f-duration").value) p.set("max_days", $("#f-duration").value);
  if ($("#f-participants").value) p.set("min_participants", $("#f-participants").value);
  if ($("#f-new").checked) p.set("new_within_days", "7");
  if ($("#f-theme").value) p.set("theme", $("#f-theme").value);
  if ($("#f-prize").value) p.set("min_prize", $("#f-prize").value);
  if ($("#f-ends").value) p.set("ends_within_days", $("#f-ends").value);
  p.set("sort", $("#f-sort").value);
  if ($("#f-untracked").checked) p.set("tracked", "false");

  const { count, total, items } = await api("/api/hackathons?" + p);
  $("#discover-count").textContent =
    `${total} lomba cocok${count < total ? ` (menampilkan ${count} teratas)` : ""}`
    + " — dibaca dari database lokal, tanpa scraping ulang.";
  $("#rows").innerHTML = items.map((h) => {
    const c = countdown(h.end_at);
    const e = h.eligibility;
    return `<tr class="${e && e.verdict === "blocked" ? "is-blocked" : ""}">
      <td><a class="title" href="${esc(h.url)}" target="_blank" rel="noopener">${esc(h.title)}</a>
        <div class="meta">${[h.organizer, h.mode, h.location].filter(Boolean).map(esc).join(" · ")}</div>
        ${eligWhy(e)}</td>
      <td class="nowrap">${eligChip(e)}</td>
      <td><span class="tag ${h.source}">${h.source}</span></td>
      <td class="nowrap"><span class="tag">${h.audience === "student" ? "mahasiswa" : "umum"}</span></td>
      <td class="nowrap ${c.cls}">${c.text}</td>
      <td class="right nowrap">${money(h.prize_amount, h.prize_currency)}</td>
      <td class="right">${h.participants ?? "—"}</td>
      <td class="right nowrap"><button class="btn sm" data-id="${esc(h.id)}">${
        h.track_status ? `<svg class="i i-xs" aria-hidden="true"><use href="#i-edit"/></svg> ` + TRACK_LABELS[h.track_status] : `<svg class="i i-xs" aria-hidden="true"><use href="#i-plus"/></svg> Lacak`}</button></td>
    </tr>`;
  }).join("") || '<tr><td colspan="8"><div class="empty">Tidak ada yang cocok. Longgarkan filternya.</div></td></tr>';
  bindCards("#rows");
}

["f-q", "f-status", "f-eligibility", "f-audience", "f-mode", "f-theme", "f-prize", "f-ends",
 "f-duration", "f-participants", "f-new", "f-sort", "f-untracked"].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener(el.type === "search" ? "input" : "change", debounce(loadDiscover, 220));
});
$("#f-reset").addEventListener("click", () => {
  ["f-q", "f-audience", "f-mode", "f-theme", "f-prize", "f-ends", "f-duration", "f-participants"]
    .forEach((id) => (document.getElementById(id).value = ""));
  $("#f-status").value = "open";
  $("#f-eligibility").value = "joinable";
  $("#f-sort").value = "deadline";
  $("#f-untracked").checked = false;
  $("#f-new").checked = false;
  discoverSources.clear();
  $$("#f-sources .chip").forEach((c) => c.classList.remove("on"));
  loadDiscover();
});

/* global search jumps into Jelajahi */
$("#global-search").addEventListener("input", debounce((e) => {
  $("#f-q").value = e.target.value;
  $("#f-status").value = "";
  $("#f-eligibility").value = "";
  if (state.page !== "discover") go("discover"); else loadDiscover();
}, 260));
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
    e.preventDefault();
    $("#global-search").focus();
  }
});

/* ---------------------------------------------------------------- digest */

async function loadDigest() {
  const list = await api("/api/digests");
  const sel = $("#d-date");
  if (!list.items.length) {
    $("#digest-body").innerHTML = `<div class="empty">Belum ada digest.
      Jalankan <code>./run_daily.sh</code> atau klik "Susun ulang" di atas.</div>`;
    $("#d-note").textContent = "";
    return;
  }
  const keep = sel.value;
  sel.innerHTML = list.items.map((d) => `<option value="${d}">${d}</option>`).join("");
  if (keep && list.items.includes(keep)) sel.value = keep;

  const data = await api("/api/digest?date=" + sel.value);
  const c = data.context;
  $("#d-note").textContent =
    `${c.candidates} kandidat lolos syaratmu · ${c.excluded_ineligible} tersaring`
    + (c.your_deadlines ? ` · ${c.your_deadlines} deadline di papanmu minggu ini` : "");

  $("#digest-body").innerHTML = `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      ${data.top.map((item, i) => digestCard(item, i + 1)).join("")}
    </div>
    <p style="color:var(--faint);font-size:11.5px;margin-top:14px">
      Disusun ${when(data.generated_at)} · profil ${esc(data.profile.country)} ·
      ${esc(LEVEL_LABELS[data.profile.student_level])} · ${esc(TRAVEL_LABELS[data.profile.travel])}.
      Skor adalah kecocokan, bukan prediksi menang.
    </p>`;
  bindCards("#digest-body");
  bindFeedback("#digest-body", loadDigest);
}

function digestCard(item, n) {
  const cls = item.score >= 85 ? "hot" : item.score >= 65 ? "mid" : "";
  const flags = [
    item.pinned ? "disematkan" : "",
    item.is_new === true ? "baru" : item.is_new === false ? "lanjutan" : "",
    item.closes_this_week ? "tutup minggu ini" : "",
  ].filter(Boolean);
  return `<div class="card reco ${item.pinned ? "pinned" : ""}" data-id="${esc(item.id)}">
    <div class="top">
      <span class="score ${cls}">${item.score}</span>
      <h4><span style="opacity:.45">${n}.</span> ${esc(item.title)}</h4>
    </div>
    <div class="reasons">${item.reasons.map((r) => `<span>${esc(r)}</span>`).join("")}</div>
    ${eligWhy(item.eligibility)}
    <div class="foot">
      ${eligChip(item.eligibility)}
      <span class="tag ${item.source}">${item.source}</span>
      ${flags.map((f) => `<span class="tag">${esc(f)}</span>`).join("")}
      <span class="due">${item.days_left != null ? item.days_left + " hari lagi" : "—"}</span>
      <span style="margin-left:auto;display:inline-flex;gap:6px">
        ${fbButtons(item)}
        <button class="btn sm" data-id="${esc(item.id)}">+ Lacak</button>
      </span>
    </div>
  </div>`;
}

$("#d-date").addEventListener("change", loadDigest);
$("#d-build").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.classList.add("is-busy");
  try {
    await send("/api/digest/build?top=5", "POST", {});
    toast("Digest disusun ulang");
  } catch (err) { toast("Gagal: " + err.message); }
  btn.disabled = false;
  btn.classList.remove("is-busy");
  loadDigest();
});

/* ---------------------------------------------------------------- scraper */

async function loadScraper() {
  await loadFacets();
  const [sources, runs, rules] = await Promise.all([
    api("/api/sources"), api("/api/runs?limit=15"), api("/api/rules/status"),
  ]);
  renderRulesStatus(rules);
  $("#s-ttl").value = sources.ttl_minutes;
  $("#s-cards").innerHTML = sources.items.map(sourceCard).join("");
  $("#runs").innerHTML = runs.items.map((r) => `<tr>
    <td class="nowrap">${when(r.finished_at)}</td>
    <td><span class="tag ${r.source}">${r.source}</span></td>
    <td class="right">${r.found}</td><td class="right">${r.inserted}</td><td class="right">${r.updated}</td>
    <td>${r.error ? `<span class="due-soon">${esc(r.error)}</span>` : '<span class="due-ok">ok</span>'}</td>
  </tr>`).join("") || '<tr><td colspan="6"><div class="empty">Belum ada riwayat.</div></td></tr>';
}

function renderRulesStatus(r) {
  const t = r.totals;
  $("#rules-status").innerHTML = `
    <div class="tablewrap" style="margin-bottom:10px">
    <table>
      <thead><tr><th>Sumber</th><th class="right">Hidup</th><th class="right">Terverifikasi</th>
        <th class="right">Tak terbaca</th><th class="right">Belum dicek</th>
        <th class="right">Larangan negara</th></tr></thead>
      <tbody>${r.by_source.map((e) => `<tr>
        <td><span class="tag ${e.source}">${e.source}</span></td>
        <td class="right">${e.live}</td>
        <td class="right"><span class="${e.verified ? "due-ok" : "due-none"}">${e.verified}</span></td>
        <td class="right"><span class="${e.unreadable ? "due-warn" : "due-none"}">${e.unreadable}</span></td>
        <td class="right"><span class="${e.pending ? "due-warn" : "due-none"}">${e.pending}</span></td>
        <td class="right">${e.with_country_limits}</td>
      </tr>`).join("")}</tbody>
    </table>
    </div>
    <span class="elig ${r.blocked_for_your_country ? "blocked" : "eligible"}">${r.blocked_for_your_country} melarang ${r.your_country}</span>
    <span class="tag">${t.verified} dari ${t.live} lomba hidup terverifikasi</span>`;
  $("#rules-run").disabled = t.pending === 0;
  $("#rules-note").textContent = t.pending
    ? `${t.pending} belum diperiksa`
    : "Semua yang bisa diperiksa sudah diperiksa.";
}

$("#rules-run").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.classList.add("is-busy");
  $("#rules-note").textContent = "membaca halaman aturan… (bisa beberapa menit)";
  try {
    const r = await send("/api/rules/check?limit=60&sources=devpost,lablab,mlh", "POST", {});
    toast(`${r.checked} aturan terbaca · ${r.restricted} punya larangan negara`);
  } catch (err) {
    toast("Gagal: " + err.message);
  }
  btn.classList.remove("is-busy");
  await loadScraper();
  loadChrome();
});

async function runScrape(opts = {}) {
  const body = {
    sources: $$("#s-sources .chip.on").map((c) => c.dataset.source),
    devpost_statuses: $$("#s-devpost-status .chip.on").map((c) => c.dataset.status),
    mlh_seasons: ($("#s-mlh-seasons").value.match(/\d{4}/g) || []).map(Number),
    max_pages: Number($("#s-pages").value) || 8,
    ttl_minutes: Number($("#s-ttl").value),
    force: $("#s-force").checked,
    ...opts,
  };
  if (!body.sources.length) { toast("Pilih minimal satu sumber"); return null; }
  if (!body.mlh_seasons.length) body.mlh_seasons = null;
  return (await send("/api/scrape", "POST", body)).results;
}

$("#s-run").addEventListener("click", async () => {
  const btn = $("#s-run");
  btn.disabled = true;
  btn.classList.add("is-busy");
  $("#s-status").textContent = "sedang mengambil data…";
  try {
    const results = await runScrape();
    if (results) {
      $("#s-result").innerHTML = results.map((r) => {
        if (r.error) return `<div class="tl"><span class="tag ${r.source}">${r.source}</span> <span class="due-soon">${esc(r.error)}</span></div>`;
        if (r.skipped) return `<div class="tl"><span class="tag ${r.source}">${r.source}</span> <span class="due-warn">dilewati — data masih segar (${ago(r.last_success)}). Centang "paksa" untuk ambil ulang.</span></div>`;
        const rules = r.rules && r.rules.total
          ? ` · aturan: ${r.rules.checked} terbaca, ${r.rules.restricted} punya larangan negara`
            + (r.rules.failed ? `, ${r.rules.failed} gagal` : "")
          : "";
        const pending = r.rules_pending
          ? ` <span class="due-warn">(sisa ${r.rules_pending} aturan belum dibaca)</span>` : "";
        return `<div class="tl"><span class="tag ${r.source}">${r.source}</span> <span class="due-ok">${r.found} ditemukan · ${r.new} baru · ${r.updated} diperbarui${rules}</span>${pending}</div>`;
      }).join("");
      toast("Scraping selesai — hasil tersimpan");
    }
  } catch (err) {
    $("#s-result").innerHTML = `<div class="due-soon">${esc(err.message)}</div>`;
  }
  $("#s-status").textContent = "";
  btn.disabled = false;
  btn.classList.remove("is-busy");
  state.facets = null;
  const keep = $$("#s-sources .chip.on").map((c) => c.dataset.source);
  await loadScraper();
  $$("#s-sources .chip").forEach((c) => c.classList.toggle("on", keep.includes(c.dataset.source)));
  loadChrome();
});

$("#quick-sync").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.classList.add("is-busy");
  $("#quick-sync-label").textContent = "sinkron…";
  try {
    const results = await send("/api/scrape", "POST", { sources: null, force: false });
    const done = results.results.filter((r) => !r.skipped && !r.error);
    toast(done.length
      ? done.map((r) => `${r.source}: +${r.new}`).join("  ")
      : "Semua sumber masih segar — pakai halaman Scraper untuk memaksa");
  } catch (err) { toast("Gagal: " + err.message); }
  btn.disabled = false;
  btn.classList.remove("is-busy");
  $("#quick-sync-label").textContent = "Sinkron";
  go(state.page);
});

/* ---------------------------------------------------------------- editor */

const toLocalInput = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
};

async function openEditor(id) {
  $("#notif-pop").hidden = true;
  const h = await api("/api/hackathons/" + encodeURIComponent(id));
  state.current = h;
  state.checklist = (h.track_checklist || []).map((i) => ({ ...i }));

  $("#ed-title").textContent = h.title;
  $("#ed-source").className = "tag " + h.source;
  $("#ed-source").textContent = h.source;
  $("#ed-link").href = h.url;
  const c = countdown(h.end_at);
  $("#ed-due").innerHTML = `<span class="${c.cls}">${c.text}</span> · ${money(h.prize_amount, h.prize_currency)}`;
  $("#ed-where").innerHTML =
    esc([AUDIENCE_LABELS[h.audience], h.mode, h.location].filter(Boolean).join(" · "))
    + " " + eligChip(h.eligibility);
  $("#ed-elig").innerHTML = eligWhy(h.eligibility)
    + (h.openness_claim && !h.eligibility_snippet
        ? `<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted);font-size:11.5px">klaim penyelenggara soal keterbukaan</summary>
           <div style="color:var(--muted);font-size:11.5px;margin-top:6px;line-height:1.5">${esc(h.openness_claim)}</div></details>`
        : "")
    + (h.eligibility_snippet
        ? `<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--muted);font-size:11.5px">kutipan aturan resminya</summary>
           <div style="color:var(--muted);font-size:11.5px;margin-top:6px;line-height:1.5">${esc(h.eligibility_snippet)}</div></details>`
        : "");
  renderEditorFeedback(h.feedback);

  $("#ed-status").value = h.track_status || "interested";
  $("#ed-priority").value = h.track_priority || 2;
  $("#ed-deadline").value = toLocalInput(h.track_my_deadline);
  $("#ed-progress").value = h.track_progress || 0;
  $("#ed-progress-val").textContent = (h.track_progress || 0) + "%";
  $("#ed-project").value = h.track_project_name || "";
  $("#ed-team").value = h.track_team || "";
  $("#ed-submission").value = h.track_submission_url || "";
  $("#ed-notes").value = h.track_notes || "";
  $("#ed-remove").hidden = !h.track_status;
  $("#ed-timeline-wrap").hidden = !h.track_status;
  renderTimeline(h.events || []);
  renderChecklist();
  $("#editor").showModal();
}

function renderChecklist() {
  const done = state.checklist.filter((i) => i.done).length;
  $("#ed-check-count").textContent = state.checklist.length ? `${done}/${state.checklist.length}` : "";
  $("#ed-checklist").innerHTML = state.checklist.map((item, i) => `
    <div class="ci ${item.done ? "done" : ""}">
      <input type="checkbox" data-check="${i}" ${item.done ? "checked" : ""}>
      <input type="text" data-text="${i}" value="${esc(item.text)}">
      <button class="x" data-del="${i}" type="button" aria-label="Hapus langkah"><svg class="i i-sm" aria-hidden="true"><use href="#i-x"/></svg></button>
    </div>`).join("");
  $$("#ed-checklist [data-check]").forEach((el) => el.addEventListener("change", () => {
    state.checklist[el.dataset.check].done = el.checked;
    renderChecklist();
  }));
  $$("#ed-checklist [data-text]").forEach((el) => el.addEventListener("input", () => {
    state.checklist[el.dataset.text].text = el.value;
  }));
  $$("#ed-checklist [data-del]").forEach((el) => el.addEventListener("click", () => {
    state.checklist.splice(Number(el.dataset.del), 1);
    renderChecklist();
  }));
}

function addChecklistItem() {
  const text = $("#ed-check-new").value.trim();
  if (!text) return;
  state.checklist.push({ text, done: false });
  $("#ed-check-new").value = "";
  renderChecklist();
}
$("#ed-check-add").addEventListener("click", addChecklistItem);
$("#ed-check-new").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addChecklistItem(); } });

$("#ed-progress").addEventListener("input", (e) => ($("#ed-progress-val").textContent = e.target.value + "%"));

function renderTimeline(events) {
  $("#ed-timeline").innerHTML = events.length
    ? events.map((e) => `<div class="tl"><span class="when">${ago(e.created_at)}</span><span>${esc(e.message)}</span></div>`).join("")
    : `<div class="tl"><span style="color:var(--faint)">belum ada riwayat</span></div>`;
}

async function addNote() {
  const message = $("#ed-note-new").value.trim();
  if (!message || !state.current) return;
  const res = await send(`/api/tracked/${encodeURIComponent(state.current.id)}/events`, "POST", { message });
  $("#ed-note-new").value = "";
  renderTimeline(res.events);
  if (state.page === "overview") loadOverview();
}
$("#ed-note-add").addEventListener("click", addNote);
$("#ed-note-new").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addNote(); } });

function renderEditorFeedback(fb) {
  state.current.feedback = fb;
  $("#ed-like").classList.toggle("on-like", fb === "like");
  $("#ed-dismiss").classList.toggle("on-dismiss", fb === "dismiss");
  $("#ed-fb-note").textContent = {
    like: "disematkan di urutan atas rekomendasi",
    dismiss: "tidak akan muncul lagi di rekomendasi",
  }[fb] || "";
}

["like", "dismiss"].forEach((kind) =>
  $(`#ed-${kind}`).addEventListener("click", async () => {
    await setFeedback(state.current.id, kind);
    renderEditorFeedback(state.feedback[state.current.id]);
  }));

$("#ed-save").addEventListener("click", async () => {
  const local = $("#ed-deadline").value;
  await send(`/api/tracked/${encodeURIComponent(state.current.id)}`, "PUT", {
    status: $("#ed-status").value,
    priority: Number($("#ed-priority").value),
    progress: Number($("#ed-progress").value),
    my_deadline: local ? new Date(local).toISOString() : null,
    project_name: $("#ed-project").value || null,
    team: $("#ed-team").value || null,
    submission_url: $("#ed-submission").value || null,
    notes: $("#ed-notes").value || null,
    checklist: state.checklist.filter((i) => i.text.trim()),
  });
  $("#editor").close();
  toast("Tersimpan");
  go(state.page);
  loadChrome();
});

$("#ed-remove").addEventListener("click", async () => {
  await api(`/api/tracked/${encodeURIComponent(state.current.id)}`, { method: "DELETE" });
  $("#editor").close();
  toast("Dihapus dari papan");
  go(state.page);
  loadChrome();
});
$("#ed-cancel").addEventListener("click", () => {
  $("#editor").close();
  go(state.page);
});

/* ---------------------------------------------------------------- boot */

loadChrome();
go("overview");
