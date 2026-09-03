/* ============ VPS-PANEL app.js ============ */
"use strict";

/* ---------- CSRF fetch wrapper ---------- */
let CSRF = null;
async function csrfToken() {
    if (!CSRF) {
        const res = await fetch("/csrf");
        const data = await res.json();
        CSRF = data.csrf;
    }
    return CSRF;
}
async function api(path, opts = {}) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== "GET") {
        opts.headers["X-CSRF-Token"] = await csrfToken();
    }
    const res = await fetch(path, opts);
    if (res.status === 401 || res.status === 403 && (await res.clone().text()).includes("CSRF")) {
        if (res.status === 401) { window.location.href = "/login"; throw new Error("unauthorized"); }
    }
    return res;
}
async function apiJSON(path, opts = {}) {
    const res = await api(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
    return data;
}
function toast(msg, isErr) {
    let el = document.getElementById("toast-el");
    if (!el) {
        el = document.createElement("div");
        el.id = "toast-el";
        el.style.cssText = "position:fixed;bottom:1rem;right:1rem;z-index:99;background:#11151f;border:1px solid #242b3d;color:#d7dce8;padding:.7rem 1rem;border-radius:8px;font-size:.8rem;max-width:320px;box-shadow:0 8px 24px rgba(0,0,0,.5);";
        document.body.appendChild(el);
    }
    el.style.borderColor = isErr ? "#f87171" : "#34d399";
    el.textContent = msg;
    el.style.display = "block";
    setTimeout(() => (el.style.display = "none"), 5000);
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- state ---------- */
let ME = null;
let currentApp = null;
let currentFile = null;
let currentPath = "";
let termWS = null;
let termOpen = false;
let metricTimer = null;
let statTimer = null;
const chartCPU = { data: [], max: 60 };
const chartRAM = { data: [], max: 60 };

/* ---------- views ---------- */
function showView(name) {
    document.getElementById("view-apps").style.display = name === "apps" ? "block" : "none";
    document.getElementById("view-workspace").style.display = name === "workspace" ? "block" : "none";
    document.getElementById("view-admin").style.display = name === "admin" ? "block" : "none";
    document.querySelectorAll(".side-btn").forEach(b => b.classList.remove("active"));
    if (name === "admin") { document.getElementById("sb-admin").classList.add("active"); loadAdmin(); }
    else { document.getElementById("sb-apps").classList.add("active"); }
    if (name === "apps") { loadApps(); }
}

/* ---------- boot ---------- */
async function boot() {
    try {
        ME = await apiJSON("/api/me");
    } catch (e) {
        window.location.href = "/login";
        return;
    }
    const badge = document.getElementById("user-badge");
    badge.innerHTML = `👤 <b class="role-${ME.role}">${esc(ME.username)}</b> <span style="color:var(--muted)">· ${esc(ME.role)}</span>`;
    if (ME.role === "admin") document.getElementById("sb-admin").style.display = "block";
    document.title = "VPS-PANEL — " + ME.username;
    loadApps();
    refreshStats();
    statTimer = setInterval(refreshStats, 3000);
}

async function refreshStats() {
    try {
        const m = await apiJSON("/api/metrics");
        const u = ME.usage;
        document.getElementById("host-stats").textContent = `host CPU ${m.host_cpu}% · RAM ${m.host_ram_mb}MB`;
        document.getElementById("st-total").textContent = m.total_apps;
        document.getElementById("st-running").textContent = m.running_apps;
        document.getElementById("st-ram").textContent = Math.round(u.ram_mb);
        document.getElementById("st-cpu").textContent = u.cpu_pct;
        document.getElementById("st-disk").textContent = Math.round(u.disk_mb);
    } catch (e) { /* ignore */ }
}

/* ---------- apps grid ---------- */
let appsCache = [];
async function loadApps() {
    try {
        appsCache = await apiJSON("/api/apps");
    } catch (e) { toast(e.message, true); return; }
    const grid = document.getElementById("apps-grid");
    grid.innerHTML = "";
    if (!appsCache.length) {
        grid.innerHTML = '<div style="color:var(--muted);padding:2rem;text-align:center;">No apps yet. Click + NEW APP to deploy your first one.</div>';
        return;
    }
    appsCache.forEach(a => {
        const card = document.createElement("div");
        card.className = "app-card";
        card.innerHTML = `
            <div class="app-name">${esc(a.name)} <span class="type-badge">${esc(a.type)}</span></div>
            <div class="app-url">/app/${esc(a.id)}/</div>
            <div class="app-meta">
                <span class="status-badge status-${esc(a.status)}">${esc(a.status.toUpperCase())}</span>
                <span style="font-size:.7rem;">${esc(a.entrypoint || "—")}</span>
            </div>`;
        card.onclick = () => openApp(a.id);
        grid.appendChild(card);
    });
}

/* ---------- new app modal ---------- */
function openNewAppModal() {
    document.getElementById("newapp-modal").style.display = "flex";
    document.getElementById("na-error").style.display = "none";
}
function closeNewAppModal() {
    document.getElementById("newapp-modal").style.display = "none";
}
function typeChanged() {
    const t = document.getElementById("na-type").value;
    const wrap = document.getElementById("na-entry-wrap");
    const entry = document.getElementById("na-entry");
    if (t === "static") { wrap.style.display = "none"; entry.value = ""; }
    else {
        wrap.style.display = "block";
        entry.placeholder = t === "python" ? "bot.py" : t === "node" ? "app.js" : "index.php";
        if (!entry.value) entry.value = t === "python" ? "main.py" : t === "node" ? "app.js" : "index.php";
    }
}
function sourceChanged() {
    const s = document.getElementById("na-source").value;
    document.getElementById("na-paste-wrap").style.display = s === "paste" ? "block" : "none";
    document.getElementById("na-zip-wrap").style.display = s === "zip" ? "block" : "none";
    document.getElementById("na-git-wrap").style.display = s === "git" ? "block" : "none";
    document.getElementById("na-template-wrap").style.display = s === "template" ? "block" : "none";
    if (s === "template" && !templatesLoaded) loadTemplates();
}
let templatesLoaded = false;
let templates = [];
async function loadTemplates() {
    try {
        templates = await apiJSON("/api/templates");
        templatesLoaded = true;
        const sel = document.getElementById("na-template");
        sel.innerHTML = "";
        templates.forEach((t, i) => {
            const o = document.createElement("option");
            o.value = t.id;
            o.textContent = `${t.name} — ${t.description}`;
            sel.appendChild(o);
        });
        if (templates.length) templateChanged();
    } catch (e) { toast(e.message, true); }
}
function templateChanged() {
    const t = templates.find(x => x.id === document.getElementById("na-template").value);
    if (!t) return;
    document.getElementById("na-type").value = t.type;
    document.getElementById("na-entry").value = t.entrypoint;
    typeChanged();
}
async function createApp() {
    const name = document.getElementById("na-name").value.trim();
    const appType = document.getElementById("na-type").value;
    const entry = document.getElementById("na-entry").value.trim();
    const source = document.getElementById("na-source").value;
    if (!name) return toast("Name required", true);
    if (source === "paste" && appType !== "static" && !entry) return toast("Entrypoint required", true);

    const fd = new FormData();
    fd.append("name", name);
    fd.append("app_type", appType);
    fd.append("entrypoint", entry);
    fd.append("source_type", source);
    fd.append("auto_restart", document.getElementById("na-autorestart").checked ? "true" : "false");
    fd.append("env_vars_json", document.getElementById("na-env").value || "{}");
    fd.append("alias", document.getElementById("na-alias").value.trim());
    if (source === "paste") fd.append("paste_code", document.getElementById("na-paste").value);
    if (source === "zip") fd.append("zip_file", document.getElementById("na-zip").files[0]);
    if (source === "git") {
        fd.append("git_url", document.getElementById("na-git-url").value.trim());
        fd.append("git_branch", document.getElementById("na-git-branch").value.trim() || "main");
    }
    if (source === "template") fd.append("template_id", document.getElementById("na-template").value);

    const errBox = document.getElementById("na-error");
    try {
        const res = await api("/api/apps", { method: "POST", body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { errBox.textContent = data.detail || `Error ${res.status}`; errBox.style.display = "block"; return; }
        errBox.style.display = "none";
        closeNewAppModal();
        toast(`App "${name}" deployed (${data.id})`);
        loadApps();
    } catch (e) { errBox.textContent = e.message; errBox.style.display = "block"; }
}

/* ---------- workspace ---------- */
async function openApp(appId) {
    try {
        currentApp = await apiJSON(`/api/apps/${appId}`);
    } catch (e) { toast(e.message, true); return; }
    showView("workspace");
    document.getElementById("ws-name").textContent = currentApp.name;
    document.getElementById("ws-type").textContent = currentApp.type;
    refreshAppStatus();
    loadAliasBox();
    document.getElementById("ws-devmode").checked = !!currentApp.dev_mode;
    wsTab("metrics");
    startMetrics();
}

async function setDevMode(on) {
    if (!currentApp) return;
    try {
        const d = await apiJSON(`/api/apps/${currentApp.id}/devmode`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: on })
        });
        currentApp.dev_mode = d.dev_mode ? 1 : 0;
        toast(on ? "DEV mode on — file changes restart the app" : "DEV mode off");
    } catch (e) { toast(e.message, true); }
}

function refreshAppStatus() {
    const badge = document.getElementById("ws-status");
    badge.className = "status-badge status-" + currentApp.status;
    badge.textContent = currentApp.status.toUpperCase();
    const start = document.getElementById("ws-start");
    const stop = document.getElementById("ws-stop");
    const open = document.getElementById("ws-open");
    if (currentApp.status === "running") {
        start.style.display = "none"; stop.style.display = "inline-flex";
        open.style.display = "inline-flex"; open.href = `/app/${currentApp.id}/`;
    } else {
        start.style.display = "inline-flex"; stop.style.display = "none";
        open.style.display = "none";
    }
}

async function controlApp(action) {
    try {
        currentApp = await apiJSON(`/api/apps/${currentApp.id}/${action}`, { method: "POST" });
        refreshAppStatus();
        toast(`App ${action} → ${currentApp.status}`);
        loadApps();
        if (action === "stop" || action === "restart") { stopMetrics(); }
        if (action === "start" || action === "restart") { startMetrics(); }
    } catch (e) { toast(e.message, true); }
}

async function deleteApp() {
    if (!confirm(`Delete app "${currentApp.name}" and ALL its files?`)) return;
    try {
        await apiJSON(`/api/apps/${currentApp.id}`, { method: "DELETE" });
        toast("App deleted");
        showView("apps");
    } catch (e) { toast(e.message, true); }
}

function loadAliasBox() {
    const box = document.getElementById("ws-alias-box");
    if (currentApp.alias) {
        document.getElementById("ws-alias-input").value = currentApp.alias;
        document.getElementById("ws-alias-url").textContent = `/p/${currentApp.alias}/`;
        document.getElementById("ws-alias-url").href = `/p/${currentApp.alias}/`;
        document.getElementById("ws-alias-url").style.display = "";
    } else {
        document.getElementById("ws-alias-input").value = "";
        document.getElementById("ws-alias-url").style.display = "none";
    }
    box.style.display = "flex";
}
async function saveAlias() {
    const alias = document.getElementById("ws-alias-input").value.trim();
    try {
        currentApp = await apiJSON(`/api/apps/${currentApp.id}/alias`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alias }),
        });
        loadAliasBox();
        toast("Alias saved");
    } catch (e) { toast(e.message, true); }
}
async function exportApp() {
    window.location.href = `/api/apps/${currentApp.id}/export`;
}

function wsTab(name) {
    document.querySelectorAll(".ws-tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    ["metrics", "files", "terminal", "logs", "env", "cron", "snapshots"].forEach(n => {
        document.getElementById("ws-" + n).style.display = n === name ? "block" : "none";
    });
    if (name === "files") loadFiles("");
    if (name === "terminal") openTerminal();
    if (name === "logs") loadLogs(false);
    if (name === "env") loadEnv();
    if (name === "metrics") startMetrics();
    if (name === "cron") loadCron();
    if (name === "snapshots") loadSnapshots();
}

/* ---------- metrics ---------- */
function startMetrics() {
    stopMetrics();
    metricTimer = setInterval(async () => {
        if (!currentApp) return;
        try {
            const m = await apiJSON(`/api/apps/${currentApp.id}/metrics`);
            document.getElementById("m-ram").textContent = m.ram_mb;
            document.getElementById("m-cpu").textContent = m.cpu_pct;
            document.getElementById("m-up").textContent = m.uptime_s;
            chartCPU.data.push(m.cpu_pct); if (chartCPU.data.length > chartCPU.max) chartCPU.data.shift();
            chartRAM.data.push(m.ram_mb); if (chartRAM.data.length > chartRAM.max) chartRAM.data.shift();
            drawChart("chart-cpu", chartCPU.data, "#22d3ee", "CPU %");
            drawChart("chart-ram", chartRAM.data, "#a78bfa", "RAM MB");
        } catch (e) { /* ignore */ }
    }, 1500);
}
function stopMetrics() { if (metricTimer) { clearInterval(metricTimer); metricTimer = null; } }
function drawChart(id, data, color, label) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = "#242b3d"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke();
    const max = Math.max(1, ...data) * 1.15;
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    data.forEach((v, i) => {
        const x = (i / chartCPU.max) * W;
        const y = H - (v / max) * (H - 8) - 4;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = color; ctx.font = "10px monospace";
    ctx.fillText(label + " max " + Math.round(max * 10) / 10, 6, 12);
}

/* ---------- files ---------- */
async function loadFiles(path) {
    currentPath = path || "";
    try {
        const data = await apiJSON(`/api/apps/${currentApp.id}/files?path=${encodeURIComponent(currentPath)}`);
        const tree = document.getElementById("file-tree");
        tree.innerHTML = "";
        const bc = document.getElementById("file-breadcrumb");
        bc.textContent = "/" + currentPath;
        bc.style.cursor = currentPath ? "pointer" : "default";
        bc.title = currentPath ? "go up" : "";
        bc.onclick = () => { if (currentPath) { const up = currentPath.split("/").slice(0, -1).join("/"); loadFiles(up); } };

        if (!data.items.length) { tree.innerHTML = '<div style="padding:.6rem .8rem;color:var(--muted);">empty directory</div>'; return; }
        data.items.forEach(it => {
            const row = document.createElement("div");
            row.className = "file-row";
            row.innerHTML = `
                <span class="fname ${it.is_dir ? "dir" : "file"}">${it.is_dir ? "📁 " : "📄 "}${esc(it.name)}${it.is_dir ? "/" : ""}</span>
                <span class="fsize" style="color:var(--muted);font-size:.7rem;">${it.is_dir ? "" : fmtSize(it.size)}</span>
                <span class="factions">
                    ${it.is_dir ? '<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();loadFiles(\'' + escPath(currentPath + "/" + it.name) + '\')">OPEN</button>'
                        : `<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();editFile('${escPath(currentPath + "/" + it.name)}')">EDIT</button>
                           <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();downloadFile('${escPath(currentPath + "/" + it.name)}')">⬇</button>`}
                    <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deleteFile('${escPath(currentPath + "/" + it.name)}')">✕</button>
                </span>`;
            if (it.is_dir) row.onclick = () => loadFiles(currentPath + "/" + it.name);
            tree.appendChild(row);
        });
    } catch (e) { toast(e.message, true); }
}
const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + "MB" : n > 1024 ? (n / 1024).toFixed(1) + "KB" : n + "B";
const escPath = (p) => p.replace(/'/g, "\\'");

async function editFile(path) {
    try {
        const res = await api(`/api/apps/${currentApp.id}/files/read?path=${encodeURIComponent(path)}`);
        if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.detail || res.status, true); return; }
        const content = await res.text();
        currentFile = path;
        document.getElementById("file-editor-path").textContent = path;
        document.getElementById("file-editor").value = content;
        document.getElementById("file-editor-wrap").style.display = "block";
        document.getElementById("file-editor").scrollIntoView({ behavior: "smooth" });
    } catch (e) { toast(e.message, true); }
}
async function saveFile() {
    if (!currentFile) return;
    try {
        const res = await api(`/api/apps/${currentApp.id}/files/write?path=${encodeURIComponent(currentFile)}`, {
            method: "POST",
            headers: { "Content-Type": "text/plain" },
            body: document.getElementById("file-editor").value,
        });
        if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.detail || res.status, true); return; }
        toast("Saved " + currentFile);
    } catch (e) { toast(e.message, true); }
}
function promptCreateItem(isDir) {
    const name = prompt(isDir ? "New folder name:" : "New file name:");
    if (!name) return;
    const path = (currentPath ? currentPath + "/" : "") + name;
    api(`/api/apps/${currentApp.id}/files/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, is_dir: !!isDir }),
    }).then(res => {
        if (!res.ok) return res.json().then(d => { throw new Error(d.detail); });
        loadFiles(currentPath);
    }).catch(e => toast(e.message, true));
}
async function deleteFile(path) {
    if (!confirm(`Delete ${path}?`)) return;
    try {
        const res = await api(`/api/apps/${currentApp.id}/files?path=${encodeURIComponent(path)}`, { method: "DELETE" });
        if (!res.ok) { const d = await res.json().catch(() => ({})); toast(d.detail || res.status, true); return; }
        loadFiles(currentPath);
    } catch (e) { toast(e.message, true); }
}
function downloadFile(path) {
    window.open(`/api/apps/${currentApp.id}/files/read?path=${encodeURIComponent(path)}`, "_blank");
}
async function uploadFile() {
    const input = document.getElementById("file-upload-input");
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("path", currentPath);
    try {
        const res = await api(`/api/apps/${currentApp.id}/files/upload`, { method: "POST", body: fd });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(d.detail || res.status);
        toast(`Uploaded ${file.name}`);
        input.value = "";
        loadFiles(currentPath);
    } catch (e) { toast(e.message, true); }
}
async function unzipFile() {
    const input = document.getElementById("file-unzip-input");
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("zip_file", file);
    fd.append("path", currentPath);
    try {
        const res = await api(`/api/apps/${currentApp.id}/files/unzip`, { method: "POST", body: fd });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(d.detail || res.status);
        toast(`Extracted ${d.files || 0} files`);
        input.value = "";
        loadFiles(currentPath);
    } catch (e) { toast(e.message, true); }
}

/* ---------- terminal ---------- */
function openTerminal() {
    if (termOpen) return;
    termOpen = true;
    document.getElementById("term-app").textContent = currentApp.id;
    const body = document.getElementById("term-body");
    body.innerHTML = "";
    const input = document.createElement("input");
    input.id = "term-input";
    document.body.appendChild(input);
    body.addEventListener("click", () => input.focus());

    const wsProto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${wsProto}://${location.host}/api/apps/${currentApp.id}/terminal/ws`);
    termWS = ws;

    ws.onopen = () => { input.focus(); sendResize(); };
    ws.onmessage = (ev) => {
        if (ev.data === "__VPS_PTY_CLOSED__") {
            appendTerm("\r\n[PTY session ended]\r\n");
            termClose();
            return;
        }
        appendTerm(ev.data);
    };
    ws.onclose = () => { if (termOpen) { appendTerm("\r\n[connection closed]\r\n"); termOpen = false; } };
    ws.onerror = () => { appendTerm("\r\n[ws error]\r\n"); };

    input.addEventListener("keydown", (e) => {
        if (!termWS || termWS.readyState !== 1) return;
        if (e.key === "Enter") { termWS.send("\r"); e.preventDefault(); }
        else if (e.key === "Backspace") { termWS.send("\x7f"); e.preventDefault(); }
        else if (e.key.length === 1) { termWS.send(e.key); e.preventDefault(); }
    });
    window.addEventListener("resize", sendResize);
}
function sendResize() {
    if (!termWS || termWS.readyState !== 1) return;
    const body = document.getElementById("term-body");
    if (!body) return;
    const cols = Math.max(20, Math.floor(body.clientWidth / 8));
    const rows = Math.max(5, Math.floor(body.clientHeight / 17));
    termWS.send(`__VPS_RESIZE__:${cols}:${rows}`);
}
function appendTerm(text) {
    const body = document.getElementById("term-body");
    body.textContent += text;
    body.scrollTop = body.scrollHeight;
}
function termClose() {
    termOpen = false;
    if (termWS) { try { termWS.close(); } catch (e) {} termWS = null; }
    const input = document.getElementById("term-input");
    if (input) input.remove();
    window.removeEventListener("resize", sendResize);
}

/* ---------- app stats (requests/bytes/process) ---------- */
async function loadAppStats() {
    if (!currentApp) return;
    try {
        const s = await apiJSON(`/api/apps/${currentApp.id}/stats`);
        document.getElementById("m-req").textContent = s.requests;
        document.getElementById("m-bytes").textContent = s.bytes_served;
        const p = s.process;
        const line = p
            ? `pid ${p.pid} · cpu ${p.cpu_pct}% · rss ${p.rss_mb}MB · up ${fmtUp(p.uptime_s)} · ${esc(p.cmd)}`
            : "no process";
        document.getElementById("m-proc").textContent = "process: " + line;
    } catch (e) { /* ignore */ }
}
function fmtUp(s) {
    if (s == null) return "?";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}

/* ---------- cron ---------- */
async function loadCron() {
    try {
        const jobs = await apiJSON(`/api/apps/${currentApp.id}/cron`);
        const box = document.getElementById("cron-list");
        box.innerHTML = "";
        if (!jobs.length) { box.innerHTML = '<div class="file-row" style="color:var(--muted)">no cron jobs yet</div>'; return; }
        jobs.forEach(j => {
            const row = document.createElement("div");
            row.className = "file-row";
            row.innerHTML = `
                <span style="flex:1; min-width:140px;"><b>${esc(j.name)}</b> <code style="color:var(--accent)">${esc(j.schedule)}</code></span>
                <span style="flex:2; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(j.command)}</span>
                <span style="width:130px; font-size:0.75rem; color:var(--muted);">next: ${esc(j.next_run || "—")}</span>
                <span style="width:130px; font-size:0.75rem; color:var(--muted);">last: ${esc((j.last_run || "—").slice(0, 19))}</span>
                <label class="switch-line" style="margin:0;"><input type="checkbox" ${j.enabled ? "checked" : ""} onchange="toggleCron('${esc(j.id)}', this.checked)"><span>on</span></label>
                <button class="btn btn-danger btn-sm" onclick="delCron('${esc(j.id)}')">✕</button>`;
            box.appendChild(row);
        });
    } catch (e) { toast(e.message, true); }
}
async function addCron() {
    const name = document.getElementById("cron-name").value.trim();
    const sched = document.getElementById("cron-sched").value.trim();
    const cmd = document.getElementById("cron-cmd").value.trim();
    if (!name || !sched || !cmd) { toast("name, schedule and command required", true); return; }
    try {
        await apiJSON(`/api/apps/${currentApp.id}/cron`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, schedule: sched, command: cmd })
        });
        toast("cron added");
        document.getElementById("cron-cmd").value = "";
        loadCron();
    } catch (e) { toast(e.message, true); }
}
async function toggleCron(id, on) {
    try { await apiJSON(`/api/apps/${currentApp.id}/cron/${id}`, { method: "POST", body: JSON.stringify({ enabled: on }) }); }
    catch (e) { toast(e.message, true); }
}
async function delCron(id) {
    if (!confirm("Delete cron job?")) return;
    try { await apiJSON(`/api/apps/${currentApp.id}/cron/${id}`, { method: "DELETE" }); loadCron(); }
    catch (e) { toast(e.message, true); }
}

/* ---------- snapshots ---------- */
async function loadSnapshots() {
    try {
        const snaps = await apiJSON(`/api/apps/${currentApp.id}/snapshots`);
        const box = document.getElementById("snapshot-list");
        box.innerHTML = "";
        if (!snaps.length) { box.innerHTML = '<div class="file-row" style="color:var(--muted)">no snapshots yet</div>'; return; }
        snaps.forEach(s => {
            const row = document.createElement("div");
            row.className = "file-row";
            row.innerHTML = `
                <span style="flex:1;"><b>📸 ${esc(s.name)}</b></span>
                <span style="width:160px; color:var(--muted); font-size:0.75rem;">${esc(s.created_at.slice(0, 19))}</span>
                <span style="width:110px; color:var(--muted); font-size:0.75rem;">${s.files} files · ${s.size_kb}KB</span>
                <button class="btn btn-accent btn-sm" onclick="restoreSnapshot('${esc(s.id)}','${esc(s.name)}')">RESTORE</button>
                <button class="btn btn-danger btn-sm" onclick="delSnapshot('${esc(s.id)}')">✕</button>`;
            box.appendChild(row);
        });
    } catch (e) { toast(e.message, true); }
}
async function createSnapshot() {
    const name = prompt("Snapshot name (optional):", "snap-" + new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-"));
    if (name === null) return;
    try {
        const d = await apiJSON(`/api/apps/${currentApp.id}/snapshots`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name.trim() || "snapshot" })
        });
        toast(`snapshot saved (${d.files} files)`);
        loadSnapshots();
    } catch (e) { toast(e.message, true); }
}
async function restoreSnapshot(id, name) {
    if (!confirm(`Restore "${name}"? Files will be replaced and the app restarted.`)) return;
    try { await apiJSON(`/api/apps/${currentApp.id}/snapshots/${id}/restore`, { method: "POST" }); toast("restored"); }
    catch (e) { toast(e.message, true); }
}
async function delSnapshot(id) {
    if (!confirm("Delete snapshot?")) return;
    try { await apiJSON(`/api/apps/${currentApp.id}/snapshots/${id}`, { method: "DELETE" }); loadSnapshots(); }
    catch (e) { toast(e.message, true); }
}

/* ---------- api tokens ---------- */
async function loadTokens() {
    try {
        const tokens = await apiJSON("/api/settings/tokens");
        const tbody = document.getElementById("token-list-body");
        tbody.innerHTML = "";
        if (!tokens.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">no tokens yet</td></tr>';
            return;
        }
        tokens.forEach(t => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><b>${esc(t.label)}</b></td>
                <td style="font-family:monospace; font-size:0.75rem;">vps_…${esc(t.token.slice(-8))}</td>
                <td style="font-size:0.75rem;">${esc(t.created_at.slice(0, 19))}</td>
                <td style="font-size:0.75rem;">${t.last_used ? esc(t.last_used.slice(0, 19)) : "never"}</td>
                <td><button class="btn btn-danger btn-sm" onclick="revokeToken('${esc(t.id)}')">REVOKE</button></td>`;
            tbody.appendChild(tr);
        });
    } catch (e) { toast(e.message, true); }
}
async function createToken() {
    const label = prompt("Token label (e.g. github-ci):", "ci");
    if (!label) return;
    try {
        const d = await apiJSON("/api/settings/tokens", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label: label.trim() })
        });
        alert(`TOKEN CREATED — copy now, it will never be shown again:\n\n${d.token}`);
        loadTokens();
    } catch (e) { toast(e.message, true); }
}
async function revokeToken(id) {
    if (!confirm("Revoke this token?")) return;
    try { await apiJSON(`/api/settings/tokens/${id}`, { method: "DELETE" }); loadTokens(); }
    catch (e) { toast(e.message, true); }
}

/* ---------- logs ---------- */
async function loadLogs(clearSearch) {
    const q = document.getElementById("log-search").value.trim();
    const url = `/api/apps/${currentApp.id}/logs?limit=400` + (q ? `&search=${encodeURIComponent(q)}` : "");
    try {
        const res = await api(url);
        const text = await res.text();
        document.getElementById("logs-body").textContent = text || "(empty)";
        document.getElementById("log-download").href = `/api/apps/${currentApp.id}/logs/download`;
    } catch (e) { toast(e.message, true); }
}

/* ---------- env ---------- */
async function loadEnv() {
    document.getElementById("env-editor").value = JSON.stringify(currentApp.env_vars || {}, null, 2);
}
async function saveEnv() {
    let env;
    try { env = JSON.parse(document.getElementById("env-editor").value); }
    catch (e) { return toast("Invalid JSON", true); }
    try {
        currentApp = await apiJSON(`/api/apps/${currentApp.id}/env`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ env }),
        });
        toast("Env saved. Restart app to apply.");
    } catch (e) { toast(e.message, true); }
}

/* ---------- admin ---------- */
async function loadAdmin() {
    try {
        const settings = await apiJSON("/api/admin/settings");
        document.getElementById("set-auto-approve").checked = settings.auto_approve;
        document.getElementById("set-ram").value = settings.default_ram;
        document.getElementById("set-cpu").value = settings.default_cpu;
        document.getElementById("set-disk").value = settings.default_disk;
        document.getElementById("set-notify-token").value = settings.notify_bot_token || "";
        document.getElementById("set-notify-chat").value = settings.notify_chat_id || "";
        document.getElementById("set-panel-name").value = settings.panel_name || "";
    } catch (e) { /* ignore */ }
    loadAdminUsers();
    loadTokens();
    loadAdminStats();
    if (adminStatTimer) clearInterval(adminStatTimer);
    adminStatTimer = setInterval(loadAdminStats, 3000);
}

let adminStatTimer = null;
async function loadAdminStats() {
    try {
        const s = await apiJSON("/api/admin/stats");
        document.getElementById("as-mem").textContent = s.mem_pct;
        document.getElementById("as-disk").textContent = s.disk_pct;
        document.getElementById("as-cpu").textContent = s.host_cpu_pct;
        document.getElementById("as-procs").textContent = (s.processes || []).length;
        const tbody = document.getElementById("admin-procs-body");
        tbody.innerHTML = "";
        (s.processes || []).forEach(p => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${p.pid}</td>
                <td><code style="font-size:0.75rem;">${esc(p.app_id || "—")}</code></td>
                <td>${p.cpu_pct}</td>
                <td>${p.rss_mb}</td>
                <td>${fmtUp(p.uptime_s)}</td>
                <td style="font-size:0.75rem; color:var(--muted); max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(p.cmd || "")}</td>`;
            tbody.appendChild(tr);
        });
    } catch (e) { /* ignore */ }
}

let adminUsers = [];
async function loadAdminUsers() {
    try {
        adminUsers = await apiJSON("/api/admin/users");
    } catch (e) { toast(e.message, true); return; }
    const tbody = document.getElementById("admin-users-body");
    tbody.innerHTML = "";
    adminUsers.forEach(u => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><b>${esc(u.username)}</b>${u.username === ME.username ? " (you)" : ""}</td>
            <td>${esc(u.role)}</td>
            <td><span class="status-badge status-${esc(u.status)}">${esc(u.status)}</span></td>
            <td>${u.apps_count}/${u.app_limit}</td>
            <td>${u.quota_ram}MB / ${u.quota_cpu}% / ${u.quota_disk}MB</td>
            <td class="usage-cell" id="usage-${esc(u.username)}">…</td>
            <td>
                ${u.status === "pending" ? `<button class="btn btn-accent btn-sm" onclick="adminAction('${esc(u.username)}','approve')">APPROVE</button>` : ""}
                ${u.status !== "banned" ? `<button class="btn btn-danger btn-sm" onclick="adminAction('${esc(u.username)}','ban')">BAN</button>` : `<button class="btn btn-secondary btn-sm" onclick="adminAction('${esc(u.username)}','unban')">UNBAN</button>`}
                ${u.username !== ME.username ? `<button class="btn btn-secondary btn-sm" onclick="openQuotaModal('${esc(u.username)}')">QUOTA</button>
                <button class="btn btn-danger btn-sm" onclick="adminDelete('${esc(u.username)}')">✕</button>` : ""}
            </td>`;
        tbody.appendChild(tr);
        apiJSON(`/api/admin/users/${encodeURIComponent(u.username)}/usage`).then(us => {
            const cell = document.getElementById("usage-" + esc(u.username));
            if (cell) cell.innerHTML = `<span class="uq">${us.ram_mb}MB</span> · ${us.cpu_pct}% · ${us.disk_mb}MB · ${us.running}run`;
        }).catch(() => {});
    });
}

async function adminAction(username, action) {
    try {
        await apiJSON(`/api/admin/users/${encodeURIComponent(username)}/${action}`, { method: "POST" });
        toast(`User ${username} → ${action}`);
        loadAdminUsers();
    } catch (e) { toast(e.message, true); }
}
async function adminDelete(username) {
    if (!confirm(`Delete user ${username} + ALL their apps?`)) return;
    try {
        await apiJSON(`/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" });
        toast("User deleted");
        loadAdminUsers();
    } catch (e) { toast(e.message, true); }
}
let quotaUser = null;
function openQuotaModal(username) {
    quotaUser = username;
    const u = adminUsers.find(x => x.username === username);
    if (!u) return;
    document.getElementById("quota-title").textContent = `Quota — ${username}`;
    document.getElementById("q-limit").value = u.app_limit;
    document.getElementById("q-ram").value = u.quota_ram;
    document.getElementById("q-cpu").value = u.quota_cpu;
    document.getElementById("q-disk").value = u.quota_disk;
    document.getElementById("quota-usage").innerHTML = "apps: <span class=\"uq\">" + u.apps_count + "</span>";
    document.getElementById("quota-modal").style.display = "flex";
}
function closeQuotaModal() { document.getElementById("quota-modal").style.display = "none"; }
async function saveQuota() {
    try {
        await apiJSON(`/api/admin/users/${encodeURIComponent(quotaUser)}/limit`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ app_limit: parseInt(document.getElementById("q-limit").value) || 0 }),
        });
        await apiJSON(`/api/admin/users/${encodeURIComponent(quotaUser)}/quota`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ram_mb: parseInt(document.getElementById("q-ram").value) || 0,
                cpu_pct: parseInt(document.getElementById("q-cpu").value) || 0,
                disk_mb: parseInt(document.getElementById("q-disk").value) || 0,
            }),
        });
        toast("Quota updated");
        closeQuotaModal();
        loadAdminUsers();
    } catch (e) { toast(e.message, true); }
}
async function saveSettings() {
    try {
        await apiJSON("/api/admin/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                auto_approve: document.getElementById("set-auto-approve").checked,
                default_ram: parseInt(document.getElementById("set-ram").value) || 512,
                default_cpu: parseInt(document.getElementById("set-cpu").value) || 100,
                default_disk: parseInt(document.getElementById("set-disk").value) || 1024,
                notify_bot_token: document.getElementById("set-notify-token").value,
                notify_chat_id: document.getElementById("set-notify-chat").value,
                panel_name: document.getElementById("set-panel-name").value,
            }),
        });
        toast("Settings saved");
    } catch (e) { toast(e.message, true); }
}
function downloadBackup() { window.location.href = "/api/admin/backup"; }
async function testAlert() {
    try {
        const d = await apiJSON("/api/admin/alert-test", { method: "POST" });
        toast(`Alert test: ${d.detail || "sent"}`);
    } catch (e) { toast(e.message, true); }
}
function openHelp() { document.getElementById("help-modal").style.display = "flex"; }
function closeHelp() { document.getElementById("help-modal").style.display = "none"; }
async function changePass() {
    const p = document.getElementById("set-new-pass").value;
    if (p.length < 6) return toast("Min 6 chars", true);
    try {
        await apiJSON("/api/admin/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_password: p }),
        });
        toast("Password changed");
        document.getElementById("set-new-pass").value = "";
    } catch (e) { toast(e.message, true); }
}

/* ---------- init ---------- */
document.addEventListener("DOMContentLoaded", boot);