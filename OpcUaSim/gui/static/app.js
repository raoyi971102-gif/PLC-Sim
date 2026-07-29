// ==========================================================================
// OpcUaSim GUI 前端
// ==========================================================================
"use strict";

// 版本 marker —— F12 Console 里能看到. 如果你看到的是旧样式但这一行没打印,
// 说明你的浏览器根本没执行这份 app.js (纯缓存旧文件).
const GUI_BUILD = "2026-07-24_warm+editables+resizer";
console.log("%c[OpcUaSim] GUI build " + GUI_BUILD, "color:#3ecf8e;font-weight:bold");

const $ = (id) => document.getElementById(id);
const el = (sel, ctx = document) => ctx.querySelector(sel);
const els = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

// ---------------- 通用 API 客户端 ----------------
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (netErr) {
    // 典型: 后端崩了/断开、CORS、DNS
    throw new Error(
      "后端连接失败 (" + netErr.message + ")。请检查启动 GUI 的那个 cmd 窗口是否有 Python traceback；" +
      "有的话把整段贴给我。"
    );
  }
  const text = await resp.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (_) { data = { ok: false, message: text }; }
  if (!resp.ok) throw new Error(data.detail || data.message || ("HTTP " + resp.status + ": " + text.slice(0, 500)));
  return data;
}

const get  = (u)      => api("GET", u);
const post = (u, b)   => api("POST", u, b || {});

function showResult(node, ok, text) {
  node.className = "result " + (ok ? "ok" : "err");
  node.textContent = text;
}

function setBusyDisabled(disabled) {
  for (const b of els("button")) {
    if (b.id === "btnClearLog") continue;    // 日志清空永远可点
    b.disabled = disabled;
  }
}

// ---------------- 状态显示 ----------------
function renderState(s) {
  $("dotMcp").className    = "dot " + (s.mcp_connected ? "on" : (s.busy === "opening" ? "busy" : ""));
  $("dotServer").className = "dot " + (s.server.running ? "on" : "");
  $("dotAgent").className  = "dot " + (s.agent.running ? "on" : "");
  const busyText = s.busy ? ("⏳ " + s.busy) : "";
  const errText  = s.last_error ? (" · ⚠ 上次错误: " + s.last_error) : "";
  $("topBusy").textContent = busyText + errText;
  $("topBusy").style.color = s.last_error ? "#ff9c94" : "#f5a623";
  $("pidServer").textContent = s.server.pid ?? "—";
  $("pidAgent").textContent  = s.agent.pid  ?? "—";
  if (s.project) $("projectPath").placeholder = s.project;

  setBusyDisabled(!!s.busy);
  ["btnServerStop","btnAgentStop","btnClose"].forEach(id => $(id).disabled = false);
}

async function refreshState() {
  try { renderState(await get("/api/state")); }
  catch (e) { console.warn("refreshState:", e); }
}

// ---------------- 标签切换 ----------------
els(".tab").forEach(t => t.addEventListener("click", () => {
  els(".tab").forEach(x => x.classList.remove("active"));
  els(".panel").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("tab-" + t.dataset.tab).classList.add("active");
}));

// ---------------- 项目栏 ----------------
$("btnOpen").onclick = async () => {
  const path = $("projectPath").value.trim();
  if (!path) return alert("请填 .project 路径");
  try {
    await post("/api/project/open", { path });
    await refreshState();
    // 打开成功后, 后台悄悄预热 (拉所有 POU/GVL/DUT 的声明+实现塞满缓存)
    // 不 await —— fire-and-forget, 让用户可以立刻操作其它 tab
    warmProjectInBackground();
  } catch (e) { alert("打开失败: " + e.message); }
};

let _warmInflight = false;
async function warmProjectInBackground() {
  if (_warmInflight) return;
  _warmInflight = true;
  try {
    // 用 fetch 而不是 alert-弹错的 post, 因为这是后台任务
    const t0 = Date.now();
    const r = await fetch("/api/project/warm", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    const dt = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[warm] ${dt}s, ${data.warmed} 对象 (POU=${data.kinds.POU}, GVL=${data.kinds.GVL}, DUT=${data.kinds.DUT})`);
    // 项目预热完 → editables 已经缓存, 自动加载列表
    try {
      const er = await get("/api/project/editables");
      _editables = er.items || [];
      renderEditables();
    } catch (_) { /* editables tab 可能还没渲染, 忽略 */ }
  } catch (e) {
    console.warn("[warm] 后台预热失败: " + e.message);
  } finally {
    _warmInflight = false;
  }
}
$("btnClose").onclick = async () => { await post("/api/project/close"); await refreshState(); };
$("btnSave").onclick    = async () => { try { const r = await post("/api/project/save");    alert(r.message || "已保存"); } catch(e) { alert(e.message); } };
$("btnCompile").onclick = async () => {
  try {
    const r = await post("/api/project/compile");
    alert((r.ok ? "✅ " : "❌ ") + r.summary + (r.raw ? ("\n\n" + r.raw) : ""));
  } catch (e) { alert(e.message); }
};
$("btnDownload").onclick = async () => {
  const strategy = $("dlStrategy").value;
  try {
    const r = await post("/api/project/download", { strategy });
    alert("下载报告:\n" + JSON.stringify(r.report, null, 2));
  } catch (e) { alert(e.message); }
};

// 拖入 .project → 自动填路径
$("projectPath").addEventListener("dragover", e => e.preventDefault());
$("projectPath").addEventListener("drop", e => {
  e.preventDefault();
  const f = e.dataTransfer.files?.[0];
  if (f && f.path) $("projectPath").value = f.path;
  else if (f) $("projectPath").value = f.name;
});

// ---------------- Tab: Extract ----------------
$("btnDiscover").onclick = async () => {
  try {
    const r = await get("/api/project/gvls");
    const list = $("gvlList");
    if (!r.gvls || !r.gvls.length) {
      list.innerHTML = "<i>未发现 GVL；请在右侧手动填路径。或点【② 编辑程序块 → 拉取项目结构】查看树。</i>";
      return;
    }
    list.innerHTML = r.gvls.map((g, i) =>
      `<label><input type="checkbox" class="gvlChk" value="${g}" checked/> <code>${g}</code></label>`
    ).join("");
  } catch (e) { alert(e.message); }
};

function collectGvls() {
  const chosen = els(".gvlChk").filter(c => c.checked).map(c => c.value);
  const manual = $("gvlManual").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  const set = new Set([...chosen, ...manual]);
  return Array.from(set);
}

function buildExtractReq(previewOnly) {
  const gvls = collectGvls();
  return {
    gvls: gvls.length ? gvls : null,
    include_all: $("chkAll").checked,
    expand_structs: $("chkExpandStructs").checked,
    ns_index: parseInt($("numNs").value, 10) || 4,
    ns_prefix: $("txtNsPrefix").value || "uniab|",
    node_language: "Chinese",
    out_path: $("txtCsvOut").value.trim() || null,
    preview_only: !!previewOnly,
  };
}

async function runExtract(previewOnly) {
  const req = buildExtractReq(previewOnly);
  try {
    const r = await post("/api/project/extract", req);
    showResult($("extractResult"), r.ok,
      `${previewOnly ? "预览" : "已写"} ${r.count} 行` +
      (r.out_path ? `\n→ ${r.out_path}` : "") +
      (r.truncated ? "\n（表格仅显示前 500 行）" : ""));
    renderTable(r.rows || []);
    // 如果不是 preview，把 CSV 路径自动带到仿真页
    if (!previewOnly && r.out_path) $("simCsv").value = r.out_path;
  } catch (e) { showResult($("extractResult"), false, e.message); }
}
$("btnPreview").onclick = () => runExtract(true);
$("btnExtract").onclick = () => runExtract(false);

function renderTable(rows) {
  const t = $("previewTable");
  if (!rows.length) { t.innerHTML = ""; return; }
  const headers = Object.keys(rows[0]);
  t.innerHTML =
    "<thead><tr>" + headers.map(h => `<th>${h}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + rows.map(r =>
      "<tr>" + headers.map(h => `<td>${escapeHtml(r[h] ?? "")}</td>`).join("") + "</tr>"
    ).join("") + "</tbody>";
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// ---------------- Tab: Edit POU ----------------
$("btnStructure").onclick = async () => {
  try {
    const r = await get("/api/project/structure");
    $("structureBox").textContent = r.text || "(空)";
  } catch (e) { alert(e.message); }
};

// 项目里发现的所有 POU/GVL/DUT 列表
let _editables = [];      // [{name, path, kind, has_impl, lang}]
let _selectedPath = null;
const KIND_ORDER = ["POU", "GVL", "DUT", "OTHER"];

function renderEditables() {
  const box = $("editablesList");
  if (!_editables.length) {
    box.innerHTML = "<div class='empty'>没找到可编辑对象</div>";
    return;
  }
  const kw = ($("editablesFilter").value || "").toLowerCase().trim();
  const kindOn = {
    POU: $("chkKindPOU").checked,
    GVL: $("chkKindGVL").checked,
    DUT: $("chkKindDUT").checked,
    OTHER: false,        // OTHER 默认不显示 (folders/tasks 等)
  };

  const grouped = {};
  for (const it of _editables) {
    if (!kindOn[it.kind]) continue;
    if (kw && !it.name.toLowerCase().includes(kw) && !it.path.toLowerCase().includes(kw)) continue;
    (grouped[it.kind] ||= []).push(it);
  }

  const html = [];
  for (const k of KIND_ORDER) {
    const items = grouped[k];
    if (!items || !items.length) continue;
    html.push(`<div class="group-hd">${k} · ${items.length}</div>`);
    for (const it of items) {
      const cls = (it.path === _selectedPath) ? "item active" : "item";
      const relPath = it.path.startsWith("Application/") ? it.path.slice("Application/".length) : it.path;
      html.push(
        `<div class="${cls}" data-path="${escapeHtml(it.path)}" data-kind="${it.kind}">` +
          `<span class="kbadge ${it.kind}">${it.kind}</span>` +
          `<span class="item-name">${escapeHtml(it.name)}</span>` +
          (relPath !== it.name ? `<span class="item-path">${escapeHtml(relPath)}</span>` : "") +
        `</div>`
      );
    }
  }
  box.innerHTML = html.join("");
  els(".editables .item").forEach(node => {
    node.onclick = () => {
      _selectedPath = node.dataset.path;
      $("pouPath").value = _selectedPath;
      $("pouKindBadge").className = "badge " + node.dataset.kind;
      $("pouKindBadge").textContent = node.dataset.kind;
      renderEditables();     // 高亮
      readPouByPath(_selectedPath);
    };
  });
}

async function loadEditables(force) {
  const btn = force ? $("btnRefreshEditables") : $("btnDiscoverEditables");
  const box = $("editablesList");
  box.innerHTML = "<div class='empty'>扫描中… (首次约 20s)</div>";
  btn.disabled = true;
  try {
    const url = "/api/project/editables" + (force ? "?refresh=true" : "");
    const r = await get(url);
    _editables = r.items || [];
    renderEditables();
  } catch (e) {
    box.innerHTML = `<div class='empty'>失败: ${escapeHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}
$("btnDiscoverEditables").onclick = () => loadEditables(false);
$("btnRefreshEditables").onclick  = () => loadEditables(true);
$("editablesFilter").oninput = renderEditables;
$("chkKindPOU").onchange = renderEditables;
$("chkKindGVL").onchange = renderEditables;
$("chkKindDUT").onchange = renderEditables;

async function readPouByPath(p) {
  try {
    const r = await get("/api/pou?path=" + encodeURIComponent(p));
    $("pouDecl").value = r.declaration || "";
    $("pouImpl").value = r.implementation || "";
  } catch (e) { alert(e.message); }
}
$("btnGetPou").onclick = async () => {
  const p = $("pouPath").value.trim();
  if (!p) return alert("请填 POU 路径");
  _selectedPath = p;
  renderEditables();
  await readPouByPath(p);
};
$("btnSetPou").onclick = async () => {
  const path = $("pouPath").value.trim();
  if (!path) return alert("请填 POU 路径");
  const body = {
    path,
    declaration: $("pouDecl").value,
    implementation: $("pouImpl").value,
    save: $("chkSetSave").checked,
    compile: $("chkSetCompile").checked,
  };
  try {
    const r = await post("/api/pou", body);
    showResult($("setPouResult"), r.ok, JSON.stringify(r, null, 2));
  } catch (e) { showResult($("setPouResult"), false, e.message); }
};

// ---------------- Tab: Sim ----------------
$("btnServerStart").onclick = async () => {
  try {
    const r = await post("/api/server/start", {
      csv: $("simCsv").value.trim() || null,
      host: $("simHost").value.trim() || "0.0.0.0",
      port: parseInt($("simPort").value, 10) || 4855,
      ns_index: parseInt($("simNs").value, 10) || 4,
      occupancy_true: $("simOcc").checked,
    });
    console.log("server started pid=", r.pid);
  } catch (e) { alert(e.message); }
};
$("btnServerStop").onclick = async () => { try { await post("/api/server/stop"); } catch(e){ alert(e.message); } };

$("btnAgentStart").onclick = async () => {
  try {
    const r = await post("/api/agent/start", {
      host: $("agentHost").value.trim() || "127.0.0.1",
      port: parseInt($("agentPort").value, 10) || 4855,
      config: $("agentCfg").value.trim() || null,
      csv: $("agentCsv").value.trim() || $("simCsv").value.trim() || null,
    });
    console.log("agent started pid=", r.pid);
  } catch (e) { alert(e.message); }
};
$("btnAgentStop").onclick = async () => { try { await post("/api/agent/stop"); } catch(e){ alert(e.message); } };

// ---------------- Tab: Pipeline ----------------
$("btnPipeline").onclick = async () => {
  const body = {
    include_all: $("plAll").checked,
    expand_structs: true,
    ns_index: parseInt($("numNs").value, 10) || 4,
    ns_prefix: $("txtNsPrefix").value || "uniab|",
    node_language: "Chinese",
    host: $("plHost").value.trim() || "0.0.0.0",
    port: parseInt($("plPort").value, 10) || 4855,
    also_start_agent: $("plAgent").checked,
    agent_config: null,
  };
  try {
    const r = await post("/api/pipeline", body);
    showResult($("pipelineResult"), r.ok, JSON.stringify(r, null, 2));
  } catch (e) { showResult($("pipelineResult"), false, e.message); }
};

// ---------------- 日志 SSE ----------------
const logBox = $("logBox");
$("btnClearLog").onclick = () => { logBox.innerHTML = ""; };

function levelValue(l) { return { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }[l] || 0; }

function appendLog(entry) {
  const min = parseInt($("logLevel").value, 10);
  if (levelValue(entry.level) < min) return;
  const filter = $("logFilter").value.trim().toLowerCase();
  if (filter && !entry.msg.toLowerCase().includes(filter) && !entry.source.toLowerCase().includes(filter)) return;
  const t = new Date(entry.ts * 1000);
  const ts = t.toTimeString().slice(0, 8) + "." + String(t.getMilliseconds()).padStart(3, "0");
  const line = document.createElement("div");
  line.className = "line " + entry.level;
  line.innerHTML = `<span class="ts">${ts}</span><span class="src">${entry.source}</span>${escapeHtml(entry.msg)}`;
  logBox.appendChild(line);
  while (logBox.children.length > 2000) logBox.removeChild(logBox.firstChild);
  if ($("autoScroll").checked) logBox.scrollTop = logBox.scrollHeight;
}

function connectSse() {
  const es = new EventSource("/api/logs/stream");
  es.addEventListener("log",   ev => { try { appendLog(JSON.parse(ev.data)); } catch(_){} });
  es.addEventListener("state", ev => { try { renderState(JSON.parse(ev.data)); } catch(_){} });
  es.onopen  = () => { $("sseState").textContent = "🟢 已连接"; };
  es.onerror = () => {
    $("sseState").textContent = "🔴 断开，5s 后重连…";
    es.close();
    setTimeout(connectSse, 5000);
  };
}

connectSse();
refreshState();
setInterval(refreshState, 4000);

// 顶栏 build badge —— 拉后端 /api/version 显示后端启动时间, 帮助判断是不是老 backend
(async function updateBuildBadge() {
  const badge = $("buildBadge");
  if (!badge) return;
  try {
    const r = await fetch("/api/version");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const v = await r.json();
    const started = new Date(v.backend_started * 1000);
    const hh = String(started.getHours()).padStart(2, "0");
    const mm = String(started.getMinutes()).padStart(2, "0");
    const ss = String(started.getSeconds()).padStart(2, "0");
    const jsMtime = v.static_mtime["app.js"] ? new Date(v.static_mtime["app.js"] * 1000) : null;
    const jsTag = jsMtime ? `${String(jsMtime.getHours()).padStart(2,"0")}:${String(jsMtime.getMinutes()).padStart(2,"0")}` : "?";
    badge.textContent = `GUI ${GUI_BUILD}  ·  backend ${hh}:${mm}:${ss} (pid ${v.backend_pid})  ·  app.js ${jsTag}`;
    badge.title = "点击查看完整 /api/version 响应";
    badge.onclick = () => alert(JSON.stringify(v, null, 2));
  } catch (e) {
    badge.textContent = "build ? (无法拉 /api/version: " + e.message + ")";
    badge.style.color = "#e0665b";
  }
})();

// ---------------- 日志窗口拖拽调高 ----------------
(function initLogResizer() {
  const resizer  = $("logResizer");
  const logbar   = $("logbar");
  const collapseBtn = $("btnLogCollapse");
  if (!resizer || !logbar) return;

  const MIN_H = 80, MAX_H = () => Math.max(200, window.innerHeight - 300);
  const LS_KEY = "opcuasim.logHeight";
  const LS_COLLAPSED = "opcuasim.logCollapsed";

  function applyHeight(h) {
    h = Math.max(MIN_H, Math.min(MAX_H(), h));
    document.documentElement.style.setProperty("--log-h", h + "px");
    logbar.style.height = h + "px";
  }
  // 恢复保存的高度
  const saved = parseInt(localStorage.getItem(LS_KEY), 10);
  if (saved && saved > MIN_H) applyHeight(saved);

  // 拖拽
  let dragging = false, startY = 0, startH = 0;
  resizer.addEventListener("mousedown", (e) => {
    if (logbar.classList.contains("collapsed")) return;
    dragging = true; startY = e.clientY;
    startH = logbar.getBoundingClientRect().height;
    resizer.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ns-resize";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    // 往上拖 = 变高 (startY - clientY 是正)
    applyHeight(startH + (startY - e.clientY));
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    resizer.classList.remove("dragging");
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    const h = logbar.getBoundingClientRect().height;
    localStorage.setItem(LS_KEY, Math.round(h));
  });

  // 折叠按钮
  function setCollapsed(c) {
    logbar.classList.toggle("collapsed", c);
    collapseBtn.textContent = c ? "▴" : "▾";
    if (!c) {
      const h = parseInt(localStorage.getItem(LS_KEY), 10) || 240;
      applyHeight(h);
    } else {
      document.documentElement.style.setProperty("--log-h", "36px");
    }
    localStorage.setItem(LS_COLLAPSED, c ? "1" : "0");
  }
  if (collapseBtn) {
    collapseBtn.onclick = () => setCollapsed(!logbar.classList.contains("collapsed"));
    if (localStorage.getItem(LS_COLLAPSED) === "1") setCollapsed(true);
  }

  // 窗口大小改变时防止 log 挤出可视区
  window.addEventListener("resize", () => {
    const cur = logbar.getBoundingClientRect().height;
    if (cur > MAX_H()) applyHeight(MAX_H());
  });
})();