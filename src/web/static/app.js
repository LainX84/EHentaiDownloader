// EHentai Downloader – web interface. Everything runs in the server: this page shows the
// state (polled from /api/state) and sends the button presses.
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const S = {
  info: null, since: 0, epoch: -1, linesRev: -1, retryRev: -1,
  lines: [], retry: [], mode: null, settings: null, editing: false, declined: false,
};

// ------------------------------------------------------------------ helpers

async function api(path, body, method) {
  const opts = { method: method || (body === undefined ? "GET" : "POST"), headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

function fmtSize(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtTime(seconds) {
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

// ------------------------------------------------------------------ modal

let modalResolve = null;

function modal(title, body, buttons, opts) {
  // buttons: [{label, value, cls}] → resolves with the value of the button pressed
  closeModal(undefined);
  $("#modal-title").textContent = title;
  const holder = $("#modal-body");
  holder.replaceChildren();
  if (typeof body === "string") holder.append(el("div", { class: "msg", text: body }));
  else if (body) holder.append(body);
  const row = $("#modal-buttons");
  row.replaceChildren();
  const dismiss = (opts && "dismiss" in opts) ? opts.dismiss : null;
  for (const b of buttons) {
    row.append(el("button", { class: `btn ${b.cls || "ghost"}`, text: b.label, onclick: async () => {
      if (b.action) {
        try { if ((await b.action()) === false) return; } catch (err) { showError(err); return; }
      }
      closeModal(b.value);
    } }));
  }
  $("#modal-back").classList.remove("hidden");
  $("#modal-back").dataset.dismiss = dismiss === undefined ? "" : JSON.stringify(dismiss);
  $("#modal-back").dataset.locked = opts && opts.locked ? "1" : "";
  const focus = row.querySelector(".accent") || row.lastChild;
  if (focus) focus.focus();
  return new Promise((resolve) => { modalResolve = resolve; });
}

function closeModal(value) {
  $("#modal-back").classList.add("hidden");
  const resolve = modalResolve;
  modalResolve = null;
  if (resolve) resolve(value);
}

$("#modal-back").addEventListener("mousedown", (e) => {
  if (e.target === $("#modal-back") && !$("#modal-back").dataset.locked) closeModal(null);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal-back").classList.contains("hidden") && !$("#modal-back").dataset.locked) closeModal(null);
});

const alertBox = (title, text) => modal(title, text, [{ label: "OK", value: true, cls: "accent" }]);
const confirmBox = (title, text, yes = "Yes", no = "No") =>
  modal(title, text, [{ label: no, value: false }, { label: yes, value: true, cls: "accent" }]);
const showError = (err) => alertBox("Error", err.message || String(err));

// ------------------------------------------------------------------ menus / tabs

for (const menu of $$(".menu")) {
  menu.querySelector(".menu-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.classList.contains("open");
    $$(".menu").forEach((m) => m.classList.remove("open"));
    if (!open) menu.classList.add("open");
  });
  menu.querySelector(".menu-list").addEventListener("click", (e) => {
    if (e.target.closest("button, a")) menu.classList.remove("open");
  });
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) $$(".menu").forEach((m) => m.classList.remove("open"));
});

function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-page").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  try { localStorage.setItem("ehd-tab", name); } catch (_) { /* private mode */ }
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
try { showTab(localStorage.getItem("ehd-tab") || "single"); } catch (_) { showTab("single"); }

const actions = {
  files: showFiles, logfile: showLogFile, account: showAccount, updates: checkUpdates,
  disclaimer: () => showDisclaimer(false), license: showLicense, about: showAbout,
};
$$("[data-action]").forEach((b) => b.addEventListener("click", () => actions[b.dataset.action]()));

// ------------------------------------------------------------------ settings

async function changeSetting(changes) {
  try {
    S.settings = await api("/api/settings", changes);
    applySettings();
  } catch (err) { showError(err); }
}

function applyTheme(theme) {
  const dark = theme === "dark" || (theme === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => S.settings && applyTheme(S.settings.theme));

function applySettings() {
  const st = S.settings;
  if (!st) return;
  $$("[data-setting]").forEach((cb) => { cb.checked = !!st[cb.dataset.setting]; });
  $$("input[name=theme]").forEach((r) => { r.checked = r.value === st.theme; });
  $$("input[name=speed]").forEach((r) => { r.checked = Number(r.value) === st.speed; });
  if (document.activeElement !== $("#format")) $("#format").value = st.image_format;
  applyTheme(st.theme);
}

$$("[data-setting]").forEach((cb) => cb.addEventListener("change", () => changeSetting({ [cb.dataset.setting]: cb.checked })));
$$("input[name=theme]").forEach((r) => r.addEventListener("change", () => changeSetting({ theme: r.value })));
$("#format").addEventListener("change", async () => {
  const value = $("#format").value;
  if (/^(JPG|PNG|WEBP)/.test(value)) {
    await alertBox("Animated Images Warning",
      "You selected a fixed image format.\nAny animated images in the album (animated WebP/GIF) will lose their " +
      "animation and only the first static frame will be saved.\n\nTip: use 'AUTO' to convert static images to JPG " +
      "while keeping animations.");
  }
  changeSetting({ image_format: value });
});
$("#speed-label").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $$(".menu")[1];
  $$(".menu").forEach((m) => m.classList.remove("open"));
  menu.classList.add("open");
});

// ------------------------------------------------------------------ single

async function startSingle() {
  const url = $("#single-url").value.trim();
  if (!url) { alertBox("Missing URL", "Please enter a gallery URL."); return; }
  try { await api("/api/single", { url }); poll(); } catch (err) { alertBox("Invalid link", err.message); }
}
$("#single-start").addEventListener("click", startSingle);
$("#single-url").addEventListener("keydown", (e) => { if (e.key === "Enter") startSingle(); });
$("#single-clear").addEventListener("click", () => { $("#single-url").value = ""; $("#single-url").focus(); });

// ------------------------------------------------------------------ batch list

async function addLinks(text) {
  if (!text.trim()) return;
  try {
    const r = await api("/api/batch/add", { text });
    if (!r.found) alertBox("No links", "No E-Hentai / ExHentai / nhentai gallery link found in the text.");
    else $("#add-text").value = "";
    poll();
  } catch (err) { showError(err); }
}
$("#add-btn").addEventListener("click", () => addLinks($("#add-text").value));
$("#add-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addLinks($("#add-text").value); }
});
$("#batch-list").addEventListener("paste", (e) => {
  e.preventDefault();
  addLinks((e.clipboardData || window.clipboardData).getData("text"));
});

function renderLines() {
  const box = $("#batch-list");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.replaceChildren();
  if (!S.lines.length) {
    box.append(el("div", { class: "empty", text: "The list is empty: paste links above (or Ctrl+V here)." }));
    return;
  }
  S.lines.forEach((line, i) => {
    box.append(el("div", { class: `line ${line.state}`, "data-id": line.id },
      el("span", { class: "no", text: String(i + 1) }),
      el("span", { class: "txt", text: line.text || " ", title: line.text }),
      el("button", { class: "del", title: "Remove this line", text: "✕", onclick: () => deleteLine(line.id) })));
  });
  const active = box.querySelector(".line.active");
  if (active && S.mode) active.scrollIntoView({ block: "nearest" });
  else if (atBottom) box.scrollTop = box.scrollHeight;
}

async function deleteLine(id) {
  try { await api(`/api/batch/line/${id}`, undefined, "DELETE"); poll(); } catch (err) { showError(err); }
}

function setEditing(on) {
  S.editing = on;
  $("#batch-list").classList.toggle("hidden", on);
  $("#batch-editor").classList.toggle("hidden", !on);
  $("#batch-edit").textContent = on ? "💾 Save" : "✏️ Edit as text";
  if (on) {
    $("#batch-editor").value = S.lines.map((l) => l.text).join("\n");
    $("#batch-editor").focus();
  }
  updateButtons();
}
$("#batch-edit").addEventListener("click", async () => {
  if (!S.editing) { setEditing(true); return; }
  try { await api("/api/batch/text", { text: $("#batch-editor").value }); setEditing(false); poll(); }
  catch (err) { showError(err); }
});
$("#batch-reload").addEventListener("click", async () => {
  if (S.editing) setEditing(false);
  try { await api("/api/batch/reload", {}); poll(); } catch (err) { showError(err); }
});
$("#batch-clear").addEventListener("click", async () => {
  const links = S.lines.filter((l) => l.text.trim() && !l.text.trim().startsWith("#")).length;
  if (!S.lines.length) return;
  if (!await confirmBox("Clear All", `Delete the whole batch list (${links} link${links !== 1 ? "s" : ""})?\n\n` +
    "URLs.txt will be emptied too. This cannot be undone.", "Delete", "Cancel")) return;
  try { await api("/api/batch/clear", {}); if (S.editing) setEditing(false); poll(); } catch (err) { showError(err); }
});
$("#batch-dedupe").addEventListener("click", async () => {
  if (S.editing) await saveEditorIfOpen();
  try {
    const p = await api("/api/batch/check", {});
    if (!p.duplicates.length) { alertBox("Duplicates", "No duplicate links found."); return; }
    if (!await confirmBox("Duplicate links", duplicatesText(p.duplicates) +
      "\n\nRemove the duplicates and keep one link per gallery?\n(When one copy of a link is malformed, the valid one is kept.)",
      "Remove", "Cancel")) return;
    await api("/api/batch/dedupe", {});
    poll();
  } catch (err) { showError(err); }
});

function duplicatesText(dups) {
  const lines = dups.slice(0, 15).map((d) => `• line ${d.line} = line ${d.kept}${d.name ? ` – ${d.name}` : ""}`);
  if (dups.length > 15) lines.push(`… and ${dups.length - 15} more`);
  return `${dups.length} link(s) point to a gallery already in the list:\n\n${lines.join("\n")}`;
}

async function saveEditorIfOpen() {
  if (!S.editing) return;
  await api("/api/batch/text", { text: $("#batch-editor").value });
  setEditing(false);
}

async function preStartChecks(action) {
  // Duplicates (remove / keep / cancel) and malformed links, like the desktop window
  await saveEditorIfOpen();
  const p = await api("/api/batch/check", {});
  poll();
  if (!p.links) { alertBox("No URLs", "URLs.txt is empty.\nAdd at least one URL before starting."); return null; }
  let remove = false;
  if (p.duplicates.length) {
    const answer = await modal("Duplicate links", duplicatesText(p.duplicates) +
      "\n\nRemove the duplicates and keep one link per gallery?\n(When one copy of a link is malformed, the valid one is kept.)",
      [{ label: "Cancel", value: "cancel" }, { label: "Keep them (they will be skipped)", value: "keep" },
        { label: "Remove", value: "remove", cls: "accent" }]);
    if (!answer || answer === "cancel") return null;
    remove = answer === "remove";
  }
  if (p.invalid.length && action) {
    let details = p.invalid.slice(0, 5).map((i) => `• line ${i.line}: ${i.error}`).join("\n\n");
    if (p.invalid.length > 5) details += `\n\n… and ${p.invalid.length - 5} more`;
    if (!await confirmBox("Invalid links", `${p.invalid.length} link(s) are not valid (highlighted in red):\n\n${details}\n\n` +
      `${action} anyway? Invalid links will fail.`)) return null;
  }
  return { remove_duplicates: remove };
}

$("#batch-start").addEventListener("click", async () => {
  try {
    const opts = await preStartChecks("Start the download");
    if (opts) { await api("/api/batch/start", opts); poll(); }
  } catch (err) { showError(err); }
});
$("#batch-verify").addEventListener("click", async () => {
  try {
    const opts = await preStartChecks(null);
    if (opts) { await api("/api/batch/verify", opts); poll(); }
  } catch (err) { showError(err); }
});
$("#sort-yes").addEventListener("click", () => api("/api/batch/sort", { accept: true }).then(poll).catch(showError));
$("#sort-no").addEventListener("click", () => api("/api/batch/sort", { accept: false }).then(poll).catch(showError));

async function togglePause() {
  try { await api("/api/pause", {}); poll(); } catch (err) { showError(err); }
}
$("#pause-btn").addEventListener("click", togglePause);
$$(".pause-twin").forEach((b) => b.addEventListener("click", togglePause));

// ------------------------------------------------------------------ retry

function renderRetry() {
  const box = $("#retry-list");
  box.replaceChildren();
  if (!S.retry.length) box.append(el("div", { class: "empty", text: "(no failed URLs found)" }));
  S.retry.forEach((item, i) => box.append(el("div", { class: `line ${item.state}` },
    el("span", { class: "no", text: String(i + 1) }), el("span", { class: "txt", text: item.text, title: item.text }))));
  const active = box.querySelector(".line.active");
  if (active) active.scrollIntoView({ block: "nearest" });
  const n = S.retry.length;
  $("#retry-count").textContent = n ? `${n} URL${n !== 1 ? "s" : ""} in queue` : "Queue is empty.";
}
$("#retry-reload").addEventListener("click", () => api("/api/retry/reload", {}).then(poll).catch(showError));
$("#retry-clear").addEventListener("click", async () => {
  if (!await confirmBox("Clear Queue", "Clear failed_downloads.txt and the queue?\nThis cannot be undone.")) return;
  api("/api/retry/clear", {}).then(poll).catch(showError);
});
$("#retry-start").addEventListener("click", async () => {
  try { await api("/api/retry/start", {}); poll(); } catch (err) { alertBox("Nothing to Retry", err.message); }
});

// ------------------------------------------------------------------ log

function appendLog(items, reset) {
  const box = $("#log");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  if (reset) box.replaceChildren();
  for (const it of items) {
    const row = el("div", { class: it.tag }, el("span", { class: "l-ts", text: `[${it.ts}] ` }));
    if (it.event) row.append(el("span", { class: "l-ev", text: `${it.event}: ` }));
    row.append(el("span", { class: "l-d", text: it.details }));
    box.append(row);
  }
  while (box.childElementCount > 3000) box.firstChild.remove();
  if (atBottom || reset) box.scrollTop = box.scrollHeight;
}
$("#log-clear").addEventListener("click", () => api("/api/log/clear", {}).then(poll).catch(showError));

// ------------------------------------------------------------------ state polling

function updateButtons() {
  const busy = !!S.mode;
  const batch = S.mode === "batch";
  $("#single-start").disabled = busy;
  $("#batch-start").disabled = busy || S.editing;
  $("#batch-verify").disabled = busy;
  $("#batch-dedupe").disabled = busy;
  $("#batch-clear").disabled = busy;
  $("#batch-reload").disabled = busy;
  $("#batch-edit").disabled = busy;
  $("#retry-start").disabled = busy;
  $("#retry-clear").disabled = S.mode === "retry";
  $("#add-btn").disabled = busy && !batch;
  $("#add-text").disabled = busy && !batch;
  $("#format").disabled = busy;
  $$("[data-setting=auto_rename], [data-setting=cbz_archive], [data-setting=comic_info]").forEach((c) => { c.disabled = busy; });
  const canPause = busy && S.mode !== "verify";
  for (const b of [$("#pause-btn"), ...$$(".pause-twin")]) {
    b.disabled = !canPause;
    b.textContent = S.paused ? "▶ Resume" : "⏸ Pause";
  }
}

function render(data) {
  S.mode = data.mode;
  S.paused = data.paused;
  if (JSON.stringify(S.settings) !== JSON.stringify(data.settings)) { S.settings = data.settings; applySettings(); }
  $("#status").textContent = data.status;
  $("#status").title = data.status;
  $("#bar1-label").textContent = data.bar1.label;
  $("#bar1").style.width = `${Math.round(data.bar1.frac * 100)}%`;
  $("#bar1-text").textContent = data.bar1.text;
  const lanes = $("#lane-bars");
  if (!lanes.childElementCount) {
    for (const lane of data.lanes) {
      lanes.append(el("div", { class: "bar-row" }, el("span", { class: "bar-label", text: `${lane.label}:` }),
        el("div", { class: "bar" }, el("div", { class: `fill ${lane.site === "nhentai" ? "nh" : "eh"}`, id: `lane-${lane.site}` })),
        el("span", { class: "bar-text", id: `lane-text-${lane.site}` })));
    }
  }
  for (const lane of data.lanes) {
    document.getElementById(`lane-${lane.site}`).style.width = `${Math.round((lane.frac || 0) * 100)}%`;
    document.getElementById(`lane-text-${lane.site}`).textContent = lane.text;
  }
  const sp = data.speed;
  $("#speed-label").textContent = `Speed: ${sp.level}x ${sp.name} (${sp.workers} parallel)`;
  $("#speed-label").classList.toggle("risky", sp.level >= 5);
  const n = data.session_images;
  $("#session").textContent = `🖼 Today: ${n.toLocaleString()} image${n !== 1 ? "s" : ""}`;
  $("#session").title = `Images downloaded since ${String(data.counter_reset_hour).padStart(2, "0")}:00 ` +
    "(the counter starts again every day at that hour)";
  if (data.ban.length) {
    const text = data.ban.map((b) => `${b.site} ${b.site === "nhentai" ? "rate limit" : "IP ban"}: resume in ${fmtTime(b.remaining)}`).join(" · ");
    $("#ban-text").textContent = `⏳ ${text}`;
    $("#ban").classList.remove("hidden");
  } else $("#ban").classList.add("hidden");

  const up = data.update;
  if (up && up.newer) {
    $("#update-banner").replaceChildren(`🆕 Version ${up.version} is available (you have ${S.info.version}). `,
      el("a", { href: up.url, target: "_blank", rel: "noopener", text: "Release notes" }),
      " — update the Docker image to get it.");
    $("#update-banner").classList.remove("hidden");
  } else $("#update-banner").classList.add("hidden");

  if (data.log.epoch !== S.epoch) { S.epoch = data.log.epoch; S.since = 0; }
  if (data.log.items.length || data.log.reset) {
    appendLog(data.log.items, data.log.reset);
    if (data.log.items.length) S.since = data.log.items[data.log.items.length - 1].seq;
  }
  if ("lines" in data) {
    S.lines = data.lines;
    S.linesRev = data.lines_rev;
    if (!S.editing) renderLines();
    $("#estimate").textContent = data.estimate;
    $("#sort-offer").classList.toggle("hidden", !data.sort_offer || !!S.mode);
  }
  if ("retry" in data) { S.retry = data.retry; S.retryRev = data.retry_rev; renderRetry(); }
  updateButtons();
}

let pollTimer = null;
let pollBusy = false;
async function poll() {
  clearTimeout(pollTimer);
  if (pollBusy) { pollTimer = setTimeout(poll, 200); return; }
  pollBusy = true;
  try {
    const q = `since=${S.since}&epoch=${S.epoch}&lines_rev=${S.linesRev}&retry_rev=${S.retryRev}`;
    render(await api(`/api/state?${q}`));
    $("#status").classList.remove("muted");
  } catch (err) {
    $("#status").textContent = `⚠ Server not reachable (${err.message}): retrying…`;
  } finally {
    pollBusy = false;
    pollTimer = setTimeout(poll, document.hidden ? 4000 : (S.mode ? 700 : 1500));
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

// ------------------------------------------------------------------ dialogs

async function showFiles() {
  let data;
  try { data = await api("/api/files"); } catch (err) { showError(err); return; }
  const body = el("div", { class: "files" },
    el("p", { class: "note", text: `Folder on the server: ${S.info.about.data_dir} (E-Hentai/, nhentai/, temp/ = albums not finished yet).` }));
  for (const [folder, entries] of Object.entries(data)) {
    body.append(el("div", { class: "folder-title", text: `${folder}/ (${entries.length})` }));
    if (!entries.length) { body.append(el("div", { class: "note", text: "empty" })); continue; }
    const table = el("table", {}, el("tr", {}, el("th", { text: "Name" }), el("th", { text: "Size" }), el("th", { text: "Date" })));
    for (const e of entries.slice(0, 300)) {
      const name = e.dir || folder === "temp"
        ? el("span", { text: `${e.dir ? "📁 " : ""}${e.name}` })
        : el("a", { href: `/api/files/download?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(e.name)}`, text: e.name });
      table.append(el("tr", {}, el("td", {}, name),
        el("td", { class: "num", text: fmtSize(e.size) + (e.dir ? ` · ${e.files} files` : "") }),
        el("td", { class: "num", text: new Date(e.mtime * 1000).toLocaleString() })));
    }
    body.append(table);
    if (entries.length > 300) body.append(el("div", { class: "note", text: `… and ${entries.length - 300} more` }));
  }
  modal("Downloads", body, [{ label: "Close", value: true, cls: "accent" }]);
}

async function showLogFile() {
  let text;
  try { text = await api("/api/log/file"); } catch (err) { showError(err); return; }
  const pre = el("pre", { text });
  modal("session.log", pre, [{ label: "Close", value: true, cls: "accent" }]);
  pre.scrollTop = pre.scrollHeight;
}

async function showAccount() {
  let info;
  try { info = await api("/api/cookies"); } catch (err) { showError(err); return; }
  const err = el("div", { class: "error-text" });
  const paste = el("textarea", { rows: 5, spellcheck: "false",
    placeholder: "Paste here the cookie rows copied from the browser (developer tools → Application / Storage → Cookies), a cookies.txt or a Cookie: header." });
  const nhKey = el("input", { type: "text", value: info.nhentai_api_key, spellcheck: "false" });
  const raw = el("textarea", { rows: 10, spellcheck: "false" });
  raw.value = info.content;
  const values = el("div", { class: "note" });
  const showValues = (v) => {
    values.textContent = Object.entries(v).map(([k, val]) => `${k}: ${val ? "✔ set" : "— missing"}`).join("   ·   ");
  };
  showValues(info.values);

  const body = el("div", {},
    el("div", { class: "section" },
      el("h4", { text: "1. Read cookies from the browser" }),
      el("p", { class: "note", text: "Log in to e-hentai.org (and exhentai.org) in your browser, open the developer tools (F12) → " +
        "Application / Storage → Cookies → the site, select the cookie rows, copy and paste them here. Only " +
        "ipb_member_id, ipb_pass_hash and igneous are kept. cookies.json is created (or updated) in the data folder." }),
      paste,
      el("div", { class: "field" }, el("label", { text: "nhentai API key (optional)" }), nhKey),
      values),
    el("div", { class: "section" },
      el("h4", { text: info.exists ? "2. cookies.json (edit it directly)" : "2. cookies.json (not created yet: template)" }),
      el("p", { class: "note", text: info.path }),
      raw),
    err);

  const reload = async () => {
    const fresh = await api("/api/cookies");
    raw.value = fresh.content;
    nhKey.value = fresh.nhentai_api_key;
    showValues(fresh.values);
  };
  modal("Account & cookies", body, [
    { label: "Close", value: true },
    { label: "Save cookies.json as edited", cls: "ghost", action: async () => {
      err.textContent = "";
      try { await api("/api/cookies/raw", { text: raw.value }); await reload(); err.textContent = "✔ cookies.json saved."; }
      catch (e) { err.textContent = e.message; }
      return false;
    } },
    { label: "Read cookies", cls: "accent", action: async () => {
      err.textContent = "";
      try {
        const r = await api("/api/cookies/paste", { text: paste.value, nhentai_api_key: nhKey.value });
        paste.value = "";
        await reload();
        err.textContent = r.found.length ? `✔ Saved: ${r.found.join(", ")}.` : "✔ Saved.";
      } catch (e) { err.textContent = e.message; }
      return false;
    } },
  ]);
}

async function checkUpdates() {
  try {
    const r = await api("/api/updates/check", {});
    alertBox("Updates", r.newer ? `Version ${r.version} is available (you have ${S.info.version}).\n${r.url}\n\nUpdate the Docker image to get it.`
      : `You have the latest version (${S.info.version}).`);
    poll();
  } catch (err) { showError(err); }
}

async function showDisclaimer(startup) {
  const d = S.info.disclaimer;
  const body = el("div", {}, el("pre", { class: "textbox", text: d.text }),
    el("p", { class: "note" }, el("a", { href: S.info.project_url, target: "_blank", rel: "noopener", text: "GitHub page" }),
      "   ", el("a", { href: S.info.issues_url, target: "_blank", rel: "noopener", text: "Report a problem" })));
  if (!startup) { modal(`⚠ ${d.title}`, body, [{ label: "Close", value: true, cls: "accent" }]); return; }
  const snooze = el("input", { type: "checkbox", id: "snooze" });
  body.append(el("label", { class: "note" }, snooze, ` Don't show this again for ${d.snooze_days} days`));
  const ok = await modal(`⚠ ${d.title}`, body, [
    { label: "Decline", value: false }, { label: "I understand and accept", value: true, cls: "accent" }], { locked: true });
  if (ok) { await api("/api/disclaimer", { snooze: snooze.checked }); return true; }
  return false;
}

async function showLicense() {
  try { modal("License", el("pre", { text: await api("/api/license") }), [{ label: "Close", value: true, cls: "accent" }]); }
  catch (err) { showError(err); }
}

function showAbout() {
  const i = S.info;
  const body = el("div", { class: "about" }, el("img", { src: "/assets/logo_small.png", alt: "" }),
    el("div", {},
      el("h3", { text: `${i.name}\nversion ${i.version}` }),
      el("p", { class: "msg", text: [
        "Downloads E-Hentai, ExHentai and nhentai galleries as image folders or CBZ archives.",
        "Web version for Docker: the downloads run in the server, also with the browser closed.", "",
        `This version: ${i.project_url}`, `Original project: ${i.upstream_url}`,
        "Developed and modified with the help of Claude AI (Anthropic).",
        "Free software under the GPL-3.0 license, provided as is, without warranty.", "",
        `Data folder: ${i.about.data_dir}`, `Settings and lists: ${i.about.config_dir}`,
        `Python ${i.about.python} · ${i.about.system}`].join("\n") })));
  modal(`About ${i.name}`, body, [{ label: "Close", value: true, cls: "accent" }]);
}

// ------------------------------------------------------------------ start

async function init() {
  try { S.info = await api("/api/info"); } catch (err) {
    $("#status").textContent = `⚠ Server not reachable: ${err.message}`;
    setTimeout(init, 3000);
    return;
  }
  $("#version").textContent = `v${S.info.version}`;
  $("#link-github").href = S.info.project_url;
  $("#link-issues").href = S.info.issues_url;
  $("#format").replaceChildren(...S.info.formats.map((f) => el("option", { value: f, text: f })));
  $("#speed-options").replaceChildren(...S.info.speeds.map((s) => {
    const radio = el("input", { type: "radio", name: "speed", value: s.level });
    radio.addEventListener("change", () => changeSetting({ speed: s.level }));
    return el("label", {}, radio, ` ${s.level}x ${s.name} (${s.workers} parallel)`);
  }));
  await poll();
  if (S.info.disclaimer.needed && !await showDisclaimer(true)) {
    document.body.replaceChildren(el("div", { class: "card", style: "margin:40px auto;max-width:560px" },
      el("h2", { text: "EHentai Downloader" }),
      el("p", { text: "You did not accept the notice. If you do not accept these terms, you are strongly advised to " +
        "stop using the program and remove the container. Reload the page to read the notice again." })));
    clearTimeout(pollTimer);
    poll = async () => {};  // eslint-disable-line no-func-assign
  }
}
init();
