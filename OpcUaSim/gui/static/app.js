// ==========================================================================
// OpcUaSim GUI 前端
// ==========================================================================
"use strict";

// 版本 marker —— F12 Console 里能看到. 如果你看到的是旧样式但这一行没打印,
// 说明你的浏览器根本没执行这份 app.js (纯缓存旧文件).
const GUI_BUILD = "2026-07-29_professional-online-vars";
console.log("%c[OpcUaSim] GUI build " + GUI_BUILD, "color:#3ecf8e;font-weight:bold");

const $ = (id) => document.getElementById(id);
const el = (sel, ctx = document) => ctx.querySelector(sel);
const els = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

// ---------------- 通用 API 客户端 ----------------
async function api(method, url, body, timeoutMs = 0) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timeoutId = controller
    ? setTimeout(() => controller.abort(), timeoutMs)
    : null;
  if (controller) opts.signal = controller.signal;

  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (netErr) {
    if (netErr.name === "AbortError") {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请刷新状态后重试`);
    }
    // 典型: 后端崩了/断开、CORS、DNS
    throw new Error(
      "后端连接失败 (" + netErr.message + ")。请检查启动 GUI 的那个 cmd 窗口是否有 Python traceback；" +
      "有的话把整段贴给我。"
    );
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
  }
  const text = await resp.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch (_) { data = { ok: false, message: text }; }
  if (!resp.ok) throw new Error(data.detail || data.message || ("HTTP " + resp.status + ": " + text.slice(0, 500)));
  return data;
}

const get  = (u)      => api("GET", u);
const post = (u, b, timeoutMs = 0) => api("POST", u, b || {}, timeoutMs);

function showResult(node, ok, text) {
  if (node.classList.contains("inline-result")) {
    node.style.color = ok ? "#86efac" : "#fda4af";
  } else {
    node.className = "result-box " + (ok ? "success" : "error");
  }
  node.textContent = text;
}

function setBusyDisabled(disabled) {
  for (const b of els("button")) {
    if (b.id === "btnClearLog") continue;    // 日志清空永远可点
    if (disabled && !b.hasAttribute("data-before-busy-disabled")) {
      b.setAttribute("data-before-busy-disabled", b.disabled ? "1" : "0");
      b.disabled = true;
    } else if (!disabled && b.hasAttribute("data-before-busy-disabled")) {
      b.disabled = b.getAttribute("data-before-busy-disabled") === "1";
      b.removeAttribute("data-before-busy-disabled");
    }
  }
}

// ---------------- 状态显示 ----------------
let lastServerRunning = false;
let currentAppState = null;

function renderState(s) {
  const firstState = currentAppState === null;
  currentAppState = s;
  $("dotMcp").className = "status-dot " + (s.mcp_connected ? "on" : (s.busy === "opening" ? "busy" : ""));
  $("dotServer").className = "status-dot " + (s.server.running ? "on" : (s.server.stopping ? "busy" : ""));
  $("dotAgent").className = "status-dot " + (s.agent.running ? "on" : (s.agent.stopping ? "busy" : ""));
  $("statusMcpText").textContent = s.mcp_connected ? "已连接" : "未连接";
  const runText = (p) =>
    p.stopping ? "停止中"
      : p.running ? (p.attached ? "运行中（外部托管）" : "运行中")
        : "已停止";
  $("statusServerText").textContent = runText(s.server);
  $("statusAgentText").textContent = runText(s.agent);

  const stateMessage = s.last_error || (s.busy ? `正在${s.busy}` : "");
  $("topBusy").textContent = stateMessage;
  $("topBusy").classList.toggle("hidden", !stateMessage);
  $("topBusy").style.color = s.last_error ? "#fda4af" : "#fcd34d";
  $("pidServer").textContent = s.server.pid ? `PID ${s.server.pid}` : "PID --";
  $("pidAgent").textContent = s.agent.pid ? `PID ${s.agent.pid}` : "PID --";
  $("serverEndpoint").textContent = s.server.endpoint || "未启动";
  if (s.project && document.activeElement !== $("projectPath")) $("projectPath").value = s.project;

  setBusyDisabled(!!s.busy);
  $("btnServerStart").disabled = !!s.busy || s.server.running || s.server.stopping;
  $("btnServerStop").disabled = !!s.busy || s.server.stopping || !s.server.running || s.server.attached;
  $("btnAgentStart").disabled = !!s.busy || s.agent.running || s.agent.stopping;
  $("btnAgentStop").disabled = !!s.busy || s.agent.stopping || !s.agent.running || s.agent.attached;
  $("btnClose").disabled = !!s.busy || !s.project;
  $("btnRefreshVars").disabled = variablePage.loading || !s.server.running;
  $("btnVarsPrev").disabled = variablePage.loading || !s.server.running || variablePage.offset <= 0;
  $("btnVarsNext").disabled =
    variablePage.loading || !s.server.running ||
    variablePage.offset + variablePage.limit >= variablePage.total;

  if (s.server.running && !lastServerRunning) {
    loadServerVariables({ reset: true });
  } else if (!s.server.running && lastServerRunning) {
    clearServerVariables("服务已停止。启动 OPC UA Server 后可继续在线读写。");
  } else if (firstState && !s.server.running) {
    clearServerVariables("启动 OPC UA Server 后即可查看和修改在线变量。");
  }
  lastServerRunning = s.server.running;
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
  el(".workspace").scrollTop = 0;
  if (t.dataset.tab === "sim" && currentAppState?.server?.running) {
    loadServerVariables();
  }
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
      list.innerHTML = "<i>未发现 GVL；可手动填写对象路径，或在“编辑程序块”中查看工程结构。</i>";
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
    html.push(`<div class="object-group">${k} · ${items.length}</div>`);
    for (const it of items) {
      const cls = (it.path === _selectedPath) ? "object-item active" : "object-item";
      const relPath = it.path.startsWith("Application/") ? it.path.slice("Application/".length) : it.path;
      html.push(
        `<div class="${cls}" data-path="${escapeHtml(it.path)}" data-kind="${it.kind}">` +
          `<span class="kind-badge">${it.kind}</span>` +
          `<strong>${escapeHtml(it.name)}</strong>` +
          (relPath !== it.name ? `<small>${escapeHtml(relPath)}</small>` : "") +
        `</div>`
      );
    }
  }
  box.innerHTML = html.join("");
  els("#editablesList .object-item").forEach(node => {
    node.onclick = () => {
      _selectedPath = node.dataset.path;
      $("pouPath").value = _selectedPath;
      $("pouKindBadge").className = "kind-badge";
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
// 远程部署时浏览器所在机器和服务器不是同一台, 填不出服务器路径 —— 上传后回填
$("simCsvFile").onchange = async (e) => {
  const input = e.target;
  const file = input.files?.[0];
  if (!file) return;
  input.disabled = true;
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(new Error("读取本地文件失败"));
      fr.readAsDataURL(file);      // 保留原始字节, 让后端去嗅探编码
    });
    const r = await post("/api/csv/upload", {
      filename: file.name,
      content_b64: dataUrl.slice(dataUrl.indexOf(",") + 1),
    }, 60000);
    $("simCsv").value = r.path;
    $("agentCsv").value = r.path;
    alert(`上传成功，识别到 ${r.count} 个变量节点：\n${r.path}`);
  } catch (err) {
    alert("上传失败: " + err.message);
  } finally {
    input.disabled = false;
    input.value = "";
  }
};

$("btnServerStart").onclick = async () => {
  try {
    const r = await post("/api/server/start", {
      csv: $("simCsv").value.trim() || null,
      host: $("simHost").value.trim() || "0.0.0.0",
      port: parseInt($("simPort").value, 10) || 4855,
      ns_index: parseInt($("simNs").value, 10) || 4,
      ns_uri: $("simNsUri").value.trim() || "urn:xuse:sim",
      occupancy_true: $("simOcc").checked,
    }, 10000);
    console.log("server started pid=", r.pid);
    await refreshState();
    await loadServerVariables({ reset: true });
  } catch (e) { alert(e.message); }
};
async function stopManagedProcess(button, url) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "停止中…";
  try {
    const result = await post(url, {}, 7000);
    if (!result.ok) throw new Error(result.message || "停止失败");
    await refreshState();
  } catch (e) {
    alert(e.message);
    await refreshState();
  } finally {
    button.textContent = originalText;
  }
}

$("btnServerStop").onclick = () => stopManagedProcess(
  $("btnServerStop"), "/api/server/stop"
);

$("btnAgentStart").onclick = async () => {
  try {
    const r = await post("/api/agent/start", {
      host: $("agentHost").value.trim() || "127.0.0.1",
      port: parseInt($("agentPort").value, 10) || 4855,
      config: $("agentCfg").value.trim() || null,
      csv: $("agentCsv").value.trim() || $("simCsv").value.trim() || null,
    }, 10000);
    console.log("agent started pid=", r.pid);
    await refreshState();
  } catch (e) { alert(e.message); }
};
$("btnAgentStop").onclick = () => stopManagedProcess(
  $("btnAgentStop"), "/api/agent/stop"
);

// ---------------- 在线变量 ----------------
const variablePage = {
  offset: 0,
  limit: 100,
  total: 0,
  query: "",
  items: [],
  loading: false,
};
let variableSearchTimer = null;

function variableMessage(text, kind = "neutral") {
  const node = $("serverVarMessage");
  node.className = `result-box ${kind}`;
  node.textContent = text;
}

function clearServerVariables(message) {
  variablePage.offset = 0;
  variablePage.total = 0;
  variablePage.items = [];
  $("serverVarCount").textContent = "0 个变量";
  $("serverVarsTable").querySelector("tbody").innerHTML =
    `<tr><td colspan="5" class="empty-cell">${escapeHtml(message || "暂无在线变量")}</td></tr>`;
  $("serverVarPageInfo").textContent = "第 0 / 0 页";
  $("btnVarsPrev").disabled = true;
  $("btnVarsNext").disabled = true;
  variableMessage(message || "暂无在线变量");
}

function renderServerVariables() {
  const tbody = $("serverVarsTable").querySelector("tbody");
  const totalPages = variablePage.total ? Math.ceil(variablePage.total / variablePage.limit) : 0;
  const currentPage = totalPages ? Math.floor(variablePage.offset / variablePage.limit) + 1 : 0;
  $("serverVarCount").textContent = `${variablePage.total} 个变量`;
  $("serverVarPageInfo").textContent = `第 ${currentPage} / ${totalPages} 页`;
  $("btnVarsPrev").disabled = variablePage.loading || variablePage.offset <= 0;
  $("btnVarsNext").disabled =
    variablePage.loading || variablePage.offset + variablePage.limit >= variablePage.total;

  if (!variablePage.items.length) {
    const text = variablePage.query ? "没有匹配的变量" : "当前 CSV 中没有可显示的变量";
    tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">${text}</td></tr>`;
    return;
  }

  tbody.innerHTML = variablePage.items.map((item, index) => {
    let editor;
    if (item.data_type === "BOOLEAN") {
      const current = item.value === true || String(item.value).toLowerCase() === "true";
      editor = `<select class="value-editor" aria-label="${escapeHtml(item.name)} 当前值">` +
        `<option value="true"${current ? " selected" : ""}>true</option>` +
        `<option value="false"${!current ? " selected" : ""}>false</option></select>`;
    } else {
      const isNumeric = ["INT16", "INT32", "FLOAT"].includes(item.data_type);
      const step = item.data_type === "FLOAT" ? "any" : "1";
      editor = `<input class="value-editor" ${isNumeric ? `type="number" step="${step}"` : `type="text"`} ` +
        `value="${escapeHtml(item.value ?? "")}" aria-label="${escapeHtml(item.name)} 当前值">`;
    }
    return `<tr data-index="${index}">` +
      `<td class="variable-name"><strong>${escapeHtml(item.name)}</strong>` +
      `<small>${escapeHtml(item.english_name || "")}</small></td>` +
      `<td><span class="type-pill">${escapeHtml(item.data_type)}</span></td>` +
      `<td>${editor}</td>` +
      `<td class="node-id">${escapeHtml(item.node_id)}</td>` +
      `<td><button class="btn small var-save">写入</button></td>` +
      `</tr>`;
  }).join("");
}

async function loadServerVariables({ reset = false } = {}) {
  if (variablePage.loading) return;
  if (!currentAppState?.server?.running) {
    clearServerVariables("启动 OPC UA Server 后即可查看和修改在线变量。");
    return;
  }
  if (reset) variablePage.offset = 0;
  variablePage.limit = parseInt($("serverVarPageSize").value, 10) || 100;
  variablePage.query = $("serverVarSearch").value.trim();
  variablePage.loading = true;
  $("btnRefreshVars").disabled = true;
  variableMessage("正在读取服务器变量…");
  try {
    const params = new URLSearchParams({
      query: variablePage.query,
      offset: String(variablePage.offset),
      limit: String(variablePage.limit),
    });
    const r = await get("/api/server/variables?" + params.toString());
    variablePage.total = r.total || 0;
    variablePage.offset = r.offset || 0;
    variablePage.items = r.items || [];
    renderServerVariables();
    variableMessage(
      variablePage.total
        ? `已从 ${currentAppState.server.endpoint || "当前服务器"} 读取 ${variablePage.items.length} 个变量。`
        : (variablePage.query ? "没有匹配的变量。" : "当前服务器没有变量。"),
      variablePage.total ? "success" : "neutral"
    );
  } catch (e) {
    variableMessage(e.message, "error");
    variablePage.items = [];
    renderServerVariables();
  } finally {
    variablePage.loading = false;
    $("btnRefreshVars").disabled = false;
    renderServerVariables();
  }
}

$("btnRefreshVars").onclick = () => loadServerVariables();
$("serverVarSearch").oninput = () => {
  clearTimeout(variableSearchTimer);
  variableSearchTimer = setTimeout(() => loadServerVariables({ reset: true }), 300);
};
$("serverVarPageSize").onchange = () => loadServerVariables({ reset: true });
$("btnVarsPrev").onclick = () => {
  variablePage.offset = Math.max(0, variablePage.offset - variablePage.limit);
  loadServerVariables();
};
$("btnVarsNext").onclick = () => {
  if (variablePage.offset + variablePage.limit < variablePage.total) {
    variablePage.offset += variablePage.limit;
    loadServerVariables();
  }
};

$("serverVarsTable").addEventListener("click", async (event) => {
  const button = event.target.closest(".var-save");
  if (!button) return;
  const row = button.closest("tr");
  const item = variablePage.items[Number(row.dataset.index)];
  const editor = row.querySelector(".value-editor");
  if (!item || !editor) return;

  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "写入中";
  try {
    const r = await post("/api/server/variable", {
      node_id: item.node_id,
      value: editor.value,
    }, 7000);
    item.value = r.value;
    editor.value = String(r.value);
    variableMessage(`${item.name} 已写入，服务器回读值为 ${String(r.value)}。`, "success");
  } catch (e) {
    variableMessage(`${item.name} 写入失败：${e.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
});

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
  line.className = "log-line " + entry.level.toLowerCase();
  line.innerHTML =
    `<span class="time">${ts}</span>` +
    `<span class="level">${escapeHtml(entry.level)}</span>` +
    `<span><b>${escapeHtml(entry.source)}</b> · ${escapeHtml(entry.msg)}</span>`;
  logBox.appendChild(line);
  while (logBox.children.length > 2000) logBox.removeChild(logBox.firstChild);
  if ($("autoScroll").checked) logBox.scrollTop = logBox.scrollHeight;
}

function connectSse() {
  const es = new EventSource("/api/logs/stream");
  es.addEventListener("log",   ev => { try { appendLog(JSON.parse(ev.data)); } catch(_){} });
  es.addEventListener("state", ev => { try { renderState(JSON.parse(ev.data)); } catch(_){} });
  es.onopen  = () => {
    $("sseState").textContent = "已连接";
    $("sseState").classList.add("on");
  };
  es.onerror = () => {
    $("sseState").textContent = "已断开，正在重连";
    $("sseState").classList.remove("on");
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
    collapseBtn.textContent = c ? "展开" : "收起";
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
