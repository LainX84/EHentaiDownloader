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
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtSpeed(bytesPerSecond) {
  return bytesPerSecond > 0 ? `${fmtSize(bytesPerSecond)}/s` : "—";
}

function fmtDuration(seconds) {
  seconds = Math.floor(seconds || 0);
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
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
  if (!document.getElementById(`tab-${name}`)) name = "single";
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-page").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${name}`));
  try { localStorage.setItem("ehd-tab", name); } catch (_) { /* private mode */ }
  if (name === "stats") setTimeout(refreshStats, 0);
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
try { showTab(localStorage.getItem("ehd-tab") || "single"); } catch (_) { showTab("single"); }

const actions = {
  logfile: showLogFile, account: showAccount, updates: checkUpdates, favorites: showFavorites,
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
  const notice = (S.info.format_notices || {})[value];
  const hidden = (S.settings && S.settings.format_notices_hidden) || [];
  let snoozed = false;
  if (notice && !hidden.includes(value)) {
    const body = el("div", {}, el("pre", { class: "textbox", text: notice }));
    const ext = (S.info.format_extensions || {})[value];
    if (ext) body.append(el("p", { class: "note" }, storeLink(ext.page, `⬇ Install ${ext.name} (Microsoft Store, free)`)));
    const snooze = el("input", { type: "checkbox" });
    body.append(el("label", { class: "note" }, snooze, ` Don't show this again for ${S.info.format_notice_snooze_days} days`));
    await modal(`Image Format: ${value}`, body, [{ label: "OK", value: true, cls: "accent" }]);
    snoozed = snooze.checked;
  }
  await changeSetting({ image_format: value });
  if (snoozed) {
    try { S.settings = await api("/api/format-notice", { format: value }); applySettings(); } catch (err) { showError(err); }
  }
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

const STATUS_ICONS = { GOOD: "✅", BAD: "❌" };

// Black or white text on a label colour (same rule as src/tags.py text_color)
function inkFor(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return "#FFFFFF";
  const [r, g, b] = m.slice(1).map((x) => parseInt(x, 16));
  return 0.299 * r + 0.587 * g + 0.114 * b > 160 ? "#1A1A1A" : "#FFFFFF";
}

// Columns: ✅ | [logo] | [ NETORARE ] | 123 | Title — the link is not shown
function lineCells(line) {
  if (!line.status) return [el("span", { class: "st" }), el("span", { class: "txt wide", text: line.title || line.text || " " })];
  const cells = [el("span", { class: "st", text: STATUS_ICONS[line.status] || "" })];
  const logo = el("img", { class: "site-logo", src: `/sitelogo/${line.site}`, alt: line.site, title: line.site });
  logo.addEventListener("error", () => logo.replaceWith(el("span", { class: "site-name", text: line.site })), { once: true });
  const badge = line.badge
    ? el("span", { class: "badge", text: line.badge, style: line.color ? `background:${line.color};color:${line.ink}` : "" })
    : el("span", {});
  cells.push(el("span", { class: "c-site" }, logo), el("span", { class: "c-tag" }, badge),
    el("span", { class: "c-pages", text: line.images ? String(line.images) : "-", title: "Images" }),
    el("span", { class: "txt", text: line.title || " " }));
  return cells;
}

function lineMovable(line) {
  return S.mode !== "verify" && S.mode !== "retry" && S.mode !== "single" && !["active", "done"].includes(line.state);
}

let dragId = null;

function renderLines() {
  const box = $("#batch-list");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  const scroll = box.scrollTop;
  box.replaceChildren();
  if (!S.lines.length) {
    box.append(el("div", { class: "empty", text: "The list is empty: paste links above (or Ctrl+V here)." }));
    return;
  }
  S.lines.forEach((line, i) => {
    const movable = lineMovable(line) && !!line.text.trim();
    const row = el("div", { class: `line ${line.state}`, "data-id": line.id, draggable: movable ? "true" : false },
      el("span", { class: "no", text: String(i + 1) }),
      ...lineCells(line),
      el("button", { class: "move", title: "Move up", text: "▲", disabled: !movable || i === 0,
        onclick: () => moveLine(line.id, i - 1) }),
      el("button", { class: "move", title: "Move down", text: "▼", disabled: !movable || i === S.lines.length - 1,
        onclick: () => moveLine(line.id, i + 1) }),
      el("button", { class: "del", title: "Remove this line", text: "✕", onclick: () => deleteLine(line.id) }));
    if (movable) {
      row.addEventListener("dragstart", (e) => { dragId = line.id; row.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
      row.addEventListener("dragend", () => {
        const dropped = dragId === null;
        dragId = null;
        row.classList.remove("dragging");
        clearDropMarks();
        if (!dropped) renderLines();  // dropped outside the list: show the latest state
      });
    }
    row.addEventListener("dragover", (e) => {
      if (dragId === null) return;
      e.preventDefault();
      const after = e.offsetY > row.clientHeight / 2;
      clearDropMarks();
      row.classList.add(after ? "drop-after" : "drop-before");
    });
    row.addEventListener("drop", (e) => {
      if (dragId === null) return;
      e.preventDefault();
      const from = S.lines.findIndex((l) => l.id === dragId);
      let to = i + (e.offsetY > row.clientHeight / 2 ? 1 : 0);
      if (from < to) to -= 1;
      const id = dragId;
      dragId = null;
      clearDropMarks();
      if (from !== to) moveLine(id, to);
    });
    box.append(row);
  });
  box.scrollTop = atBottom ? box.scrollHeight : scroll;
  // Follow the downloads only when none of them is on screen: with two sites at once the
  // list would otherwise jump between them
  const actives = [...box.querySelectorAll(".line.active")];
  const visible = (row) => row.offsetTop >= box.scrollTop && row.offsetTop + row.offsetHeight <= box.scrollTop + box.clientHeight;
  if (S.mode && actives.length && !actives.some(visible)) actives[0].scrollIntoView({ block: "nearest" });
}

function clearDropMarks() {
  $$("#batch-list .drop-before, #batch-list .drop-after").forEach((r) => r.classList.remove("drop-before", "drop-after"));
}

async function moveLine(id, toIndex) {
  try { await api(`/api/batch/line/${id}/move`, { to_index: toIndex }); poll(); } catch (err) { showError(err); }
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
    el("span", { class: "no", text: String(i + 1) }), ...lineCells(item))));
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

// Microsoft Store page: on Windows open only the small install window of the Store app,
// elsewhere the web page of the product
const IS_WINDOWS = /Windows/i.test(navigator.userAgent);
function storeLink(url, text) {
  const id = (url.match(/^https:\/\/apps\.microsoft\.com\/detail\/([a-z0-9]+)/i) || [])[1];
  const href = id && IS_WINDOWS ? `ms-windows-store://pdp/?ProductId=${id.toUpperCase()}&mode=mini` : url;
  return el("a", { href, target: "_blank", rel: "noopener", text: text || url });
}

// Log text with clickable links
const URL_RE = /https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)]/g;
function linkify(text) {
  const span = el("span", { class: "l-d" });
  let pos = 0;
  for (const m of text.matchAll(URL_RE)) {
    span.append(text.slice(pos, m.index), storeLink(m[0]));
    pos = m.index + m[0].length;
  }
  span.append(text.slice(pos));
  return span;
}

function appendLog(items, reset) {
  const box = $("#log");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  if (reset) box.replaceChildren();
  for (const it of items) {
    const row = el("div", { class: it.tag }, el("span", { class: "l-ts", text: `[${it.ts}] ` }));
    if (it.event) row.append(el("span", { class: "l-ev", text: `${it.event}: ` }));
    row.append(linkify(it.details || ""));
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
  $$("[data-setting=auto_rename], [data-setting=cbz_archive], [data-setting=comic_info], [data-setting=tag_folders]")
    .forEach((c) => { c.disabled = busy; });
  $("[data-action=favorites]").disabled = busy;
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
  $("#speed-now").textContent = data.speed_now > 0 ? `⬇ ${fmtSpeed(data.speed_now)}` : "";
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
  document.documentElement.style.setProperty("--tag-col", `${(data.badge_width || 4) + 2}ch`);
  if ("lines" in data) {
    S.lines = data.lines;
    S.linesRev = data.lines_rev;
    if (!S.editing && dragId === null) renderLines();
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
document.addEventListener("visibilitychange", () => { if (!document.hidden) { poll(); refreshStats(); } });

// ------------------------------------------------------------------ statistics

const STAT_ROWS = [
  ["Galleries · images", (b) => `${b.galleries.toLocaleString()} · ${b.images.toLocaleString()}`],
  ["Downloaded", (b) => `${fmtSize(b.bytes)} in ${fmtDuration(b.busy_seconds)}`],
  ["Speed average · peak", (b) => `${fmtSpeed(b.average_speed)} · ${fmtSpeed(b.peak_speed)}`],
];
let statsTimer = null;
let statsTagsKey = "";

async function refreshStats() {
  clearTimeout(statsTimer);
  if ($("#tab-stats").classList.contains("hidden")) return;
  try {
    const d = await api("/api/stats");
    let now = `⬇ Now: ${fmtSpeed(d.current_speed)}`;
    if (d.downloading) now += ` · ${d.downloading} galler${d.downloading !== 1 ? "ies" : "y"} downloading`;
    $("#stats-now").textContent = now;
    $("#stats-total-title").textContent = `Since ${d.first_start.slice(0, 10)}`;
    $("#stats-session-title").textContent = `This session (${d.session_started.slice(-5)})`;
    const rows = STAT_ROWS.map(([label, fn]) => el("tr", {}, el("td", { text: label }),
      el("td", { text: fn(d.total) }), el("td", { text: fn(d.session) })));
    Object.keys(d.site_labels).forEach((site, i) => {
      const cell = (b) => `${b.sites[site].galleries.toLocaleString()} · ${b.sites[site].images.toLocaleString()} · ${fmtSize(b.sites[site].bytes)}`;
      rows.push(el("tr", { class: `site${i === 0 ? " first-site" : ""}` }, el("td", { text: d.site_labels[site] }),
        el("td", { text: cell(d.total) }), el("td", { text: cell(d.session) })));
    });
    rows.push(el("tr", { class: "site" }, el("td", { class: "muted", colspan: "3", text: "Sites: galleries · images · size" })));
    $("#stats-body").replaceChildren(...rows);
    const top = d.total.top_tags;
    const key = JSON.stringify(top);
    if (key !== statsTagsKey) {
      statsTagsKey = key;
      const most = Math.max(1, ...top.map((t) => t.count));
      $("#stats-tags").replaceChildren(...(top.length ? top.map((t) => el("div", { class: "tag-row" },
        el("span", { class: "name", text: t.tag, title: t.tag }), el("span", { class: "count", text: String(t.count) }),
        el("div", { class: "bar-mini", style: `width:${Math.max(4, Math.round(t.count / most * 100))}%` })))
        : [el("div", { class: "empty", text: "No completed gallery yet: the tags of every downloaded gallery are counted here." })]));
    }
  } catch (err) {
    $("#stats-now").textContent = `⚠ ${err.message}`;
  }
  statsTimer = setTimeout(refreshStats, document.hidden ? 5000 : 1000);
}
$("#stats-reset").addEventListener("click", async () => {
  if (!await confirmBox("Reset statistics", "Delete every statistic (since the first start and this session)?\n" +
    "This cannot be undone.", "Reset", "Cancel")) return;
  try { await api("/api/stats/reset", {}); statsTagsKey = ""; refreshStats(); } catch (err) { showError(err); }
});

// ------------------------------------------------------------------ favorite tags

async function showFavorites() {
  let data;
  try { data = await api("/api/tags"); } catch (err) { showError(err); return; }
  const rows = new Map(data.tags.map((t) => [t.tag, { ...t }]));
  let order = data.tags.filter((t) => t.favorite).map((t) => t.tag);
  const width = data.badge_max;
  const badgeText = (label) => label.replace(/\|/g, "/").split(/\s+/).join(" ").trim().toUpperCase().slice(0, width).trim();

  const filter = el("input", { type: "text", placeholder: "🔍 Filter", spellcheck: "false" });
  const add = el("input", { type: "text", placeholder: "Add a tag (e.g. artist:name = Label)", spellcheck: "false" });
  const count = el("div", { class: "note" });
  const table = el("div", { class: "list fav-list" });
  const folders = el("input", { type: "checkbox" });
  folders.checked = !!(S.settings && S.settings.tag_folders);

  function draw() {
    const needle = filter.value.trim().toLowerCase();
    const others = [...rows.values()].filter((r) => !r.favorite)
      .sort((a, b) => (a.suggested - b.suggested) || (b.seen - a.seen) || a.tag.localeCompare(b.tag));
    const list = [...order.map((t) => rows.get(t)), ...others];
    table.replaceChildren(el("div", { class: "fav-row head" }, el("span", { text: "★" }), el("span", { text: "Tag" }),
      el("span", { text: "Label" }), el("span", { text: "Galleries" }), el("span", { text: "" })));
    list.forEach((r) => {
      if (needle && !r.tag.includes(needle) && !r.label.toLowerCase().includes(needle)) return;
      const box = el("input", { type: "checkbox" });
      box.checked = r.favorite;
      const badge = el("span", { class: "badge", text: badgeText(r.label), style: r.color ? `background:${r.color};color:${inkFor(r.color)}` : "" });
      box.addEventListener("change", () => {
        r.favorite = box.checked;
        if (r.favorite && !r.color) {
          const used = new Set(order.map((t) => rows.get(t).color));
          r.color = data.colors.find((c) => !used.has(c)) || data.colors[order.length % data.colors.length];
        }
        order = r.favorite ? [...order, r.tag] : order.filter((t) => t !== r.tag);
        draw();
      });
      let label = el("span", {});
      let moves = el("span", {});
      let color = el("span", {});
      if (r.favorite) {
        color = el("input", { type: "color", value: r.color || "#C2185B", title: "Colour of the label" });
        color.addEventListener("input", () => {
          r.color = color.value.toUpperCase();
          badge.style.background = r.color;
          badge.style.color = inkFor(r.color);
        });
        label = el("input", { type: "text", value: r.label, spellcheck: "false", maxlength: "40", title: "Label shown in the lists and folder name" });
        label.addEventListener("change", () => { r.label = label.value.trim() || r.tag.split(":").pop(); draw(); });
        const i = order.indexOf(r.tag);
        moves = el("span", { class: "fav-moves" },
          el("button", { class: "move", text: "▲", title: "Higher priority", disabled: i === 0,
            onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); } }),
          el("button", { class: "move", text: "▼", title: "Lower priority", disabled: i === order.length - 1,
            onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); } }));
      }
      table.append(el("label", { class: `fav-row${r.favorite ? " fav" : ""}${r.suggested ? " suggested" : ""}` },
        box,
        el("span", { class: "fav-tag" }, r.tag, r.favorite ? badge : null),
        el("span", { class: "fav-label" }, label, color),
        el("span", { class: "fav-seen", text: r.seen ? String(r.seen) : (r.suggested ? "suggested" : "") }),
        moves));
    });
    count.textContent = `${order.length} favorite${order.length !== 1 ? "s" : ""} · ${data.seen} tag${data.seen !== 1 ? "s" : ""} met in your galleries`;
  }
  filter.addEventListener("input", draw);
  const addTag = () => {
    const [left, ...rest] = add.value.split("=");
    const tag = left.trim().toLowerCase().replace(/[\s_]+/g, " ").replace(/\s*:\s*/, ":");
    if (!tag) return;
    const name = tag.split(":").pop();
    const key = rows.has(tag) ? tag : (!tag.includes(":") && rows.has(name) ? name : tag);
    if (!rows.has(key)) rows.set(key, { tag: key, label: rest.join("=").trim() || name, color: "", favorite: false, seen: 0, suggested: false });
    const r = rows.get(key);
    if (rest.join("=").trim()) r.label = rest.join("=").trim();
    if (!r.favorite) {
      const used = new Set(order.map((t) => rows.get(t).color));
      r.color = r.color || data.colors.find((c) => !used.has(c)) || data.colors[0];
      r.favorite = true;
      order.push(key);
    }
    add.value = "";
    filter.value = "";
    draw();
  };
  add.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } });
  draw();

  const body = el("div", { class: "fav-dialog" },
    el("p", { class: "note", text: "Tick a tag to make it a favorite. The list fills up by itself with the tags of every gallery " +
      "you verify or download (and how many galleries had them); a few common tags are offered from the start " +
      "(\"suggested\"), and you can add any tag yourself, e.g. an artist (artist:name)." }),
    el("p", { class: "note", text: `Favorites show as a coloured label in the lists; when a gallery has more than one, the highest ` +
      `wins (▲ ▼). Each favorite has its own label (capitals, max ${width} characters, also the folder name) and colour.` }),
    el("div", { class: "fav-tools" }, filter, add, el("button", { class: "btn ghost", text: "＋ Add", onclick: addTag })),
    table, count,
    el("label", { class: "note" }, folders, " Sort downloads into folders by favorite tag (<site>/<label>/<gallery>)"),
    el("p", { class: "note", text: "Galleries without a favorite tag stay in E-Hentai/ or nhentai/. Lines already in the list get " +
      "their label after Verify Links." }));
  await modal("Favorite tags", body, [
    { label: "Cancel", value: false },
    { label: "Save", value: true, cls: "accent", action: async () => {
      const favorites = order.map((t) => ({ tag: t, label: rows.get(t).label, color: rows.get(t).color }));
      S.settings = await api("/api/settings", { favorite_tags: favorites, tag_folders: folders.checked });
      applySettings();
      poll();
    } },
  ]);
}

// ------------------------------------------------------------------ dialogs

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
        "Free software under the GPL-3.0 license, provided as is, without warranty.",
        i.open_formats_note, "",
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
