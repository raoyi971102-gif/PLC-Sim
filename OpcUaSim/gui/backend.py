"""
gui.backend —— OpcUaSim 一体化 Web 控制面板
============================================================================
一进程内集成：
  * InoProShop MCP 桥（打开/编辑/编译/下载/结构探查/GVL 提取 → CSV）
  * OPC UA Server 子进程管理（server.py）
  * 握手代理子进程管理（handshake_agent.py）
  * 全局实时日志（SSE 推送到前端）

进程模型：
  - 一个 FastAPI 应用常驻，UI 通过 HTTP+SSE 交互
  - 长阻塞任务（open/compile/download/extract）用 asyncio.to_thread 卸到线程池
  - 一次只允许一个 MCP 长任务；用 asyncio.Lock 串行化
  - Server / Agent 是独立子进程；stdout/stderr 由后台线程转发到主 logger
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 允许直接 `python -m gui.backend` 时找到根包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common import default_csv_path
from ino_mcp.client import McpClient, McpError
from ino_mcp.config import resolve_mcp_config
from ino_mcp.toolkit import InoToolkit, DownloadStrategy
from ino_mcp.extractor import (
    extract_gvl_variables,
    find_gvl_paths,
    parse_gvl_declaration,
    write_csv,
    _to_csv_rows,
    list_editables_from_dump,
    build_dut_registry_from_dump,
    build_dut_registry_from_warm,
    parse_warm_dump,
)


# ---------------------------------------------------------------------------
# 全局日志 → SSE 桥
# ---------------------------------------------------------------------------
class _SseLogHandler(logging.Handler):
    """跨线程安全的 SSE 日志广播。
    子进程读线程 / McpClient 读线程都会走到 emit()，
    必须用 loop.call_soon_threadsafe 把 put_nowait 调回主事件循环，
    否则会破坏 asyncio.Queue 内部状态导致 uvicorn 静默崩溃。
    """
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": record.created,
            "level": record.levelname,
            "source": record.name,
            "msg": record.getMessage(),
        }
        loop = _STATE.loop
        if loop is None or not loop.is_running():
            return
        for q in list(_STATE.log_queues):
            try:
                loop.call_soon_threadsafe(_safe_put, q, entry)
            except RuntimeError:
                # loop closed
                pass
            except Exception:  # noqa: BLE001
                pass


def _safe_put(q: "asyncio.Queue", entry: dict) -> None:
    try:
        q.put_nowait(entry)
    except asyncio.QueueFull:
        # 丢弃最老的一条，塞进新的
        try:
            q.get_nowait()
            q.put_nowait(entry)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _install_root_logger() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 控制台
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, _SseLogHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"))
        root.addHandler(sh)
    # SSE
    if not any(isinstance(h, _SseLogHandler) for h in root.handlers):
        root.addHandler(_SseLogHandler())
    # 降噪
    logging.getLogger("opcua").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
@dataclass
class AppState:
    mcp: Optional[McpClient] = None
    toolkit: Optional[InoToolkit] = None
    current_project: Optional[str] = None
    busy: Optional[str] = None
    server_proc: Optional[subprocess.Popen] = None
    agent_proc: Optional[subprocess.Popen] = None
    last_extract_csv: Optional[str] = None
    last_extract_count: int = 0
    log_queues: Set[asyncio.Queue] = field(default_factory=set)
    mcp_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loop: Optional[asyncio.AbstractEventLoop] = None
    last_error: Optional[str] = None
    # dump_all_declarations 的缓存 (供 editables + extract 复用, 避免 20s 探针跑两次)
    declarations_dump: Optional[str] = None
    editables_cache: Optional[List[Dict[str, Any]]] = None
    warm_raw: Optional[str] = None            # 上次 warm_all_code 的原始输出 (供 /api/project/warm/raw 排障)

    def snapshot(self) -> Dict[str, Any]:
        def _alive(p: Optional[subprocess.Popen]) -> Optional[int]:
            return p.pid if p and p.poll() is None else None

        return {
            "project": self.current_project,
            "mcp_connected": self.mcp is not None,
            "busy": self.busy,
            "server": {
                "pid": _alive(self.server_proc),
                "running": _alive(self.server_proc) is not None,
            },
            "agent": {
                "pid": _alive(self.agent_proc),
                "running": _alive(self.agent_proc) is not None,
            },
            "last_extract_csv": self.last_extract_csv,
            "last_extract_count": self.last_extract_count,
            "last_error": self.last_error,
        }


_STATE = AppState()
log = logging.getLogger("gui")


# ---------------------------------------------------------------------------
# MCP 配置解析
# ---------------------------------------------------------------------------
def _load_mcp_defaults(server_name: str = "codesys_local") -> Dict[str, Any]:
    return resolve_mcp_config(server_name=server_name)


# ---------------------------------------------------------------------------
# 子进程 stdout 转发到 logger
# ---------------------------------------------------------------------------
def _pipe_to_logger(proc: subprocess.Popen, logger_name: str) -> None:
    lg = logging.getLogger(logger_name)

    def _reader(stream, level: int):
        for raw in iter(stream.readline, b""):
            try:
                text = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                text = repr(raw)
            if text:
                lg.log(level, text)

    threading.Thread(target=_reader, args=(proc.stdout, logging.INFO),
                     name=f"{logger_name}-out", daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, logging.WARNING),
                     name=f"{logger_name}-err", daemon=True).start()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    _install_root_logger()
    _STATE.loop = asyncio.get_running_loop()
    _STATIC_DIR_LOCAL = Path(__file__).resolve().parent / "static"
    log.info("OpcUaSim GUI started, static=%s", _STATIC_DIR_LOCAL)
    try:
        yield
    finally:
        log.info("shutting down…")
        _stop_subprocess("server_proc")
        _stop_subprocess("agent_proc")
        if _STATE.mcp is not None:
            try:
                _STATE.mcp.close()
            except Exception:  # noqa: BLE001
                pass


app = FastAPI(title="OpcUaSim Control Panel", version="1.0.0", lifespan=_lifespan)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# 关闭浏览器缓存 —— 开发阶段不容许浏览器复用旧 CSS/JS
# (曾经出现: 改了 CSS/JS 用户看不到; 因为 Ctrl+F5 也未必绕过 disk cache)
@app.middleware("http")
async def _no_cache_for_static(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# -- 首页 ------------------------------------------------------------------
# 用 mtime 作 cache-busting: <link href="style.css?v=1234567"> —— 文件一改, URL 就变,
# 无论浏览器如何积极缓存都会重新拉。
def _bust(fname: str) -> str:
    try:
        return str(int((_STATIC_DIR / fname).stat().st_mtime))
    except FileNotFoundError:
        return "0"


@app.get("/")
async def _root() -> HTMLResponse:
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/style.css"',
                        f'href="/static/style.css?v={_bust("style.css")}"')
    html = html.replace('src="/static/app.js"',
                        f'src="/static/app.js?v={_bust("app.js")}"')
    return HTMLResponse(html)


# -- 状态 ------------------------------------------------------------------
@app.get("/api/state")
async def api_state() -> Dict[str, Any]:
    return _STATE.snapshot()


@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "ts": time.time()}


# 后端启动的墙钟时间, 用于判定"你现在跑的是不是老 backend"
_BACKEND_START_TS = time.time()


@app.get("/api/version")
async def api_version() -> Dict[str, Any]:
    """诊断: 报告后端启动时间 + 静态资源 mtime + 是否有新增端点。
    页面右上角 buildBadge 会显示 backend_started 的 mm:ss, 一眼判断是不是老 backend。
    """
    def _mtime(name: str) -> Optional[float]:
        p = _STATIC_DIR / name
        return p.stat().st_mtime if p.exists() else None

    return {
        "backend_started": _BACKEND_START_TS,
        "backend_pid": os.getpid(),
        "static_mtime": {
            "index.html": _mtime("index.html"),
            "style.css":  _mtime("style.css"),
            "app.js":     _mtime("app.js"),
        },
        # 一个方案 A 版本才有的 endpoint 列表 —— 老 backend 不会有这些
        "has_endpoints": {
            "/api/project/editables": True,
            "/api/project/warm":      True,
            "/api/project/cache":     True,
        },
    }


# -- 项目 ------------------------------------------------------------------
class OpenReq(BaseModel):
    path: str
    bundle: Optional[str] = None
    codesys_path: Optional[str] = None
    codesys_profile: Optional[str] = None
    workspace: Optional[str] = None
    node: Optional[str] = None


@app.post("/api/project/open")
async def api_project_open(req: OpenReq) -> Dict[str, Any]:
    proj = str(Path(req.path).resolve())
    if not Path(proj).exists():
        raise HTTPException(400, f".project 不存在: {proj}")

    async with _STATE.mcp_lock:
        # 若已经连着别的项目, 先关掉
        if _STATE.mcp is not None:
            log.info("先关闭旧 MCP: %s", _STATE.current_project)
            await asyncio.to_thread(_STATE.mcp.close)
            _STATE.mcp = None
            _STATE.toolkit = None
            _STATE.current_project = None
        # 新项目, 清缓存
        _STATE.declarations_dump = None
        _STATE.editables_cache = None

        cfg = _load_mcp_defaults()
        if req.bundle:
            cfg["bundle_js"] = req.bundle
        if req.codesys_path:
            cfg["codesys_path"] = req.codesys_path
        if req.codesys_profile:
            cfg["codesys_profile"] = req.codesys_profile
        cfg["workspace"] = req.workspace or str(Path(proj).parent)
        if req.node:
            cfg["node_cmd"] = req.node

        if not cfg["bundle_js"] or not Path(cfg["bundle_js"]).exists():
            raise HTTPException(
                400,
                "找不到 MCP bundle.min.js；请设置 OPCUASIM_MCP_BUNDLE、"
                "配置 MCP JSON，或在请求中指定 bundle",
            )
        if not cfg["codesys_path"] or not Path(cfg["codesys_path"]).exists():
            raise HTTPException(
                400,
                "找不到 InoProShop.exe；请设置 OPCUASIM_INOPROSHOP_EXE "
                "或在请求中指定 codesys_path",
            )

        _STATE.busy = "opening"
        try:
            mcp = McpClient(bundle_js=cfg["bundle_js"], codesys_path=cfg["codesys_path"],
                            codesys_profile=cfg["codesys_profile"], workspace=cfg["workspace"],
                            node_cmd=cfg["node_cmd"])
            await asyncio.to_thread(mcp.start)
            tk = InoToolkit(mcp, proj)
            out = await asyncio.to_thread(tk.open_project)
            _STATE.mcp = mcp
            _STATE.toolkit = tk
            _STATE.current_project = proj
            log.info("项目已打开: %s", proj)
            return {"ok": True, "message": out.strip(), "state": _STATE.snapshot()}
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            log.exception("open_project 失败: %s", err)
            _STATE.last_error = err
            with contextlib.suppress(Exception, NameError):
                mcp.close()  # type: ignore[has-type,used-before-def]
            _STATE.mcp = None
            _STATE.toolkit = None
            _STATE.current_project = None
            raise HTTPException(500, err)
        finally:
            _STATE.busy = None


@app.post("/api/project/close")
async def api_project_close() -> Dict[str, Any]:
    async with _STATE.mcp_lock:
        if _STATE.mcp is not None:
            await asyncio.to_thread(_STATE.mcp.close)
        _STATE.mcp = None
        _STATE.toolkit = None
        _STATE.current_project = None
        _STATE.declarations_dump = None
        _STATE.editables_cache = None
        log.info("已断开 MCP")
        return {"ok": True, "state": _STATE.snapshot()}


async def _ensure_declarations_dump(force: bool = False) -> str:
    """拿 dump_all_declarations 的结果 (带缓存, 供 editables + extract 复用)。"""
    if not force and _STATE.declarations_dump:
        return _STATE.declarations_dump
    tk = _require_tk()
    _STATE.busy = "scanning"
    try:
        dump = await asyncio.to_thread(tk.dump_all_declarations)
    finally:
        _STATE.busy = None
    _STATE.declarations_dump = dump
    _STATE.editables_cache = None   # 让 editables 端点重算
    return dump


def _synth_dump_from_warm_entries(entries) -> str:
    """把 warm_all_code 的解析结果反过来合成一份跟 dump_all_declarations 输出格式一致的字符串。
    这样 extract 端点里现有的 build_dut_registry_from_dump 逻辑无需改就能复用。
    """
    parts = []
    for e in entries:
        parts.append("===DECL_BEGIN===")
        parts.append("PATH: " + e.path)
        parts.append("IMPL: " + ("1" if e.has_impl else "0"))
        parts.append("MIXIN: <from-warm>")
        parts.append("---BODY---")
        parts.append(e.declaration)
        parts.append("===DECL_END===")
    return "\n".join(parts)


@app.get("/api/project/editables")
async def api_project_editables(refresh: bool = False) -> Dict[str, Any]:
    """列出项目里所有可编辑对象 (POU / GVL / DUT)。带缓存 —— 首次约 20s (跑 IronPython 探针),
    后续瞬时；refresh=true 强制重跑。
    """
    _require_tk()
    if not refresh and _STATE.editables_cache is not None:
        return {"ok": True, "cached": True, "items": _STATE.editables_cache}
    async with _STATE.mcp_lock:
        dump = await _ensure_declarations_dump(force=refresh)
        items_dc = list_editables_from_dump(dump)
        items = [
            {"name": e.name, "path": e.path, "kind": e.kind,
             "has_impl": e.has_implementation, "lang": e.lang}
            for e in items_dc
        ]
        _STATE.editables_cache = items
        return {"ok": True, "cached": False, "items": items}


@app.post("/api/project/warm")
async def api_project_warm() -> Dict[str, Any]:
    """项目预热: 一次探针把所有 POU/GVL/DUT 的声明 + 实现全部拉回来并塞满缓存。

    项目打开成功后前端 fire-and-forget 调这个 —— 之后:
      - 单独读任何 POU/GVL 都 <50ms 命中 pou_code cache
      - editables 列表已经在 _STATE.editables_cache 里
      - extract 时的 DUT registry 也已经建好, 秒出

    ~20s (跟 dump_all_declarations 同数量级, 因为都是 walk 一遍 Application)。
    """
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "warming"
        try:
            warm_text = await asyncio.to_thread(tk.warm_all_code)
            _STATE.warm_raw = warm_text
            entries = parse_warm_dump(warm_text)
            paths = [e.path for e in entries]

            # 排障: 从 raw 里 grep 出 WALK 到的对象, 对比 emit 出来的对象, 找出差集
            import re as _re
            walked = _re.findall(r"^WALK\s+(.+)$", warm_text, _re.MULTILINE)
            not_emitted = _re.findall(r"^NOT_EMITTED\s+(.+)$", warm_text, _re.MULTILINE)
            skipped = _re.findall(r"^(SKIP_[A-Z_]+|OBJ_ERR|DEC_TEXT_ERR)\s+(.+?)(?::|$)",
                                  warm_text, _re.MULTILINE)
            if len(walked) != len(entries):
                log.warning("[warm] walk=%d 个对象 → emit=%d 个. NOT_EMITTED=%s SKIP=%s",
                            len(walked), len(entries), not_emitted, skipped)
                log.warning("[warm] 缺失: %s", set(walked) - set(paths))

            # 1) editables cache
            _STATE.editables_cache = [
                {"name": e.path.rsplit("/", 1)[-1], "path": e.path, "kind": e.kind,
                 "has_impl": e.has_impl, "lang": e.lang}
                for e in entries
            ]
            # 2) pou_code cache
            tk.prefill_pou_code_cache(
                [(e.path, e.declaration, e.implementation) for e in entries]
            )
            # 3) declarations_dump cache
            _STATE.declarations_dump = _synth_dump_from_warm_entries(entries)
            log.info("[warm] 项目预热完成: %d 对象 (walk=%d), cache=%s",
                     len(entries), len(walked), tk.cache.stats())
            return {"ok": True, "warmed": len(entries), "walked": len(walked),
                    "not_emitted": not_emitted,
                    "cache": tk.cache.stats(),
                    "kinds": {
                        "POU": sum(1 for e in entries if e.kind == "POU"),
                        "GVL": sum(1 for e in entries if e.kind == "GVL"),
                        "DUT": sum(1 for e in entries if e.kind == "DUT"),
                    }}
        finally:
            _STATE.busy = None


@app.get("/api/project/warm/raw", response_class=PlainTextResponse)
async def api_project_warm_raw() -> str:
    """诊断: 返回上次 warm_all_code 的原始输出 (供人肉排查为什么某些对象没被识别)。"""
    if _STATE.warm_raw is None:
        return "(还未跑过 warm — 先 POST /api/project/warm)"
    return _STATE.warm_raw


@app.get("/api/project/cache")
async def api_project_cache() -> Dict[str, Any]:
    """报告当前 toolkit + backend 的缓存命中情况 (调试 / GUI 显示用)。"""
    tk = _STATE.toolkit
    return {
        "toolkit": tk.cache.stats() if tk else None,
        "backend": {
            "declarations_dump": _STATE.declarations_dump is not None,
            "editables": len(_STATE.editables_cache) if _STATE.editables_cache else 0,
        },
    }


def _require_tk() -> InoToolkit:
    if _STATE.toolkit is None:
        raise HTTPException(400, "请先打开一个 .project 项目")
    return _STATE.toolkit


@app.post("/api/project/save")
async def api_project_save() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "saving"
        try:
            out = await asyncio.to_thread(tk.save_project)
            return {"ok": True, "message": out.strip()}
        finally:
            _STATE.busy = None


@app.post("/api/project/compile")
async def api_project_compile() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "compiling"
        try:
            cr = await asyncio.to_thread(tk.compile_project)
            return {"ok": cr.ok, "summary": cr.summary, "raw": cr.raw[-2000:] if cr.raw else ""}
        finally:
            _STATE.busy = None


class DownloadReq(BaseModel):
    strategy: str = "save_compile"    # 或 "online"


@app.post("/api/project/download")
async def api_project_download(req: DownloadReq) -> Dict[str, Any]:
    tk = _require_tk()
    try:
        strat = DownloadStrategy(req.strategy)
    except ValueError:
        raise HTTPException(400, f"未知 strategy: {req.strategy}")
    async with _STATE.mcp_lock:
        _STATE.busy = "downloading"
        try:
            report = await asyncio.to_thread(tk.download_program, strat)
            return {"ok": "error" not in report, "report": report}
        finally:
            _STATE.busy = None


@app.get("/api/project/structure")
async def api_project_structure() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "structure"
        try:
            text = await asyncio.to_thread(tk.get_project_structure)
            return {"ok": True, "text": text}
        finally:
            _STATE.busy = None


@app.get("/api/project/gvls")
async def api_project_gvls() -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "gvls"
        try:
            text = await asyncio.to_thread(tk.get_project_structure)
            gvls = find_gvl_paths(text)
            return {"ok": True, "gvls": gvls, "structure": text}
        finally:
            _STATE.busy = None


class ExtractReq(BaseModel):
    gvls: Optional[List[str]] = None
    include_all: bool = False
    ns_index: int = 4
    ns_prefix: str = "uniab|"
    node_language: str = "Chinese"      # CSV NodeLanguage 列的固定值
    out_path: Optional[str] = None      # 默认 extracted/<projectname>.csv
    preview_only: bool = False          # True 时只返回 rows, 不写盘
    expand_structs: bool = True         # False 时不自动拉 DUT registry (只展开 ARRAY)


@app.post("/api/project/extract")
async def api_project_extract(req: ExtractReq) -> Dict[str, Any]:
    tk = _require_tk()
    proj = Path(_STATE.current_project or "extracted")
    default_out = _ROOT / "extracted" / (proj.stem + ".csv")
    out_path = Path(req.out_path).resolve() if req.out_path else default_out

    async with _STATE.mcp_lock:
        _STATE.busy = "extracting"
        try:
            # 如果本次会话已经跑过 dump (比如用户先点了 '发现对象'), 直接复用它构造 registry;
            # 省一次 20s 探针
            dut_registry = None
            if req.expand_structs and _STATE.declarations_dump:
                dut_registry = build_dut_registry_from_dump(_STATE.declarations_dump)
                auto_build = False
            else:
                auto_build = req.expand_structs

            leaves = await asyncio.to_thread(
                extract_gvl_variables, tk,
                gvl_paths=req.gvls, include_all=req.include_all,
                dut_registry=dut_registry,
                auto_build_dut_registry=auto_build,
            )
            rows = _to_csv_rows(leaves, ns_index=req.ns_index,
                                ns_prefix=req.ns_prefix,
                                node_language=req.node_language)
            if not req.preview_only:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(write_csv, leaves, out_path,
                                        ns_index=req.ns_index,
                                        ns_prefix=req.ns_prefix,
                                        node_language=req.node_language)
                _STATE.last_extract_csv = str(out_path)
                _STATE.last_extract_count = len(rows)
            return {
                "ok": True,
                "count": len(rows),
                "out_path": str(out_path) if not req.preview_only else None,
                "rows": rows[:500],           # 前 500 行预览
                "truncated": len(rows) > 500,
            }
        finally:
            _STATE.busy = None


# -- POU 编辑 --------------------------------------------------------------
@app.get("/api/pou")
async def api_pou_get(path: str = Query(...)) -> Dict[str, Any]:
    tk = _require_tk()
    async with _STATE.mcp_lock:
        _STATE.busy = "reading_pou"
        try:
            raw = await asyncio.to_thread(tk.get_pou_code, path)
            decl, impl = _split_pou_output(raw)
            return {"ok": True, "path": path, "declaration": decl, "implementation": impl, "raw": raw}
        finally:
            _STATE.busy = None


def _split_pou_output(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    lo = text.lower()
    d = lo.find("declaration:")
    i = lo.find("implementation:")
    if d < 0 and i < 0:
        return text.strip(), ""
    decl_start = d + len("declaration:") if d >= 0 else 0
    decl_end = i if (i > d and i >= 0) else len(text)
    decl = text[decl_start:decl_end].strip()
    impl = text[i + len("implementation:"):].strip() if i >= 0 else ""
    return decl, impl


class PouSetReq(BaseModel):
    path: str
    declaration: Optional[str] = None
    implementation: Optional[str] = None
    save: bool = False
    compile: bool = False


@app.post("/api/pou")
async def api_pou_set(req: PouSetReq) -> Dict[str, Any]:
    tk = _require_tk()
    if req.declaration is None and req.implementation is None:
        raise HTTPException(400, "declaration 与 implementation 至少给一个")
    async with _STATE.mcp_lock:
        _STATE.busy = "writing_pou"
        result: Dict[str, Any] = {}
        try:
            out = await asyncio.to_thread(tk.set_pou_code, req.path,
                                          declaration=req.declaration,
                                          implementation=req.implementation)
            result["set"] = out.strip()
            if req.save:
                result["save"] = (await asyncio.to_thread(tk.save_project)).strip()
            if req.compile:
                cr = await asyncio.to_thread(tk.compile_project)
                result["compile"] = {"ok": cr.ok, "summary": cr.summary}
            return {"ok": True, **result}
        finally:
            _STATE.busy = None


# -- Server / Agent 子进程 -------------------------------------------------
def _find_python_exe() -> str:
    """探测真 python.exe，跳过 WindowsApps 存根。"""
    env_py = os.environ.get("PYTHON")
    if env_py and Path(env_py).exists():
        return env_py
    for cand in (
        r"D:\miniforge3\envs\unilab\python.exe",
        r"D:\miniforge3\python.exe",
    ):
        if Path(cand).exists():
            return cand
    # 兜底：如果 backend 就是被真 python 启的, 直接用 sys.executable
    if sys.executable and "WindowsApps" not in sys.executable:
        return sys.executable
    return "python"


def _python_subprocess_env() -> Dict[str, str]:
    """确保 Windows 子进程日志统一为 UTF-8，避免 GUI 中中文乱码。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _stop_subprocess(field_name: str) -> Dict[str, Any]:
    proc: Optional[subprocess.Popen] = getattr(_STATE, field_name)
    if proc is None or proc.poll() is not None:
        setattr(_STATE, field_name, None)
        return {"ok": True, "message": "已经停止或未运行"}
    log.info("终止子进程 %s pid=%d", field_name, proc.pid)
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}
    setattr(_STATE, field_name, None)
    return {"ok": True, "message": "已停止"}


class ServerStartReq(BaseModel):
    csv: Optional[str] = None     # 不给则用上次提取结果或内置演示表
    host: str = "0.0.0.0"
    port: int = 4855
    ns_index: int = 4
    ns_uri: str = "urn:xuse:sim"
    occupancy_true: bool = True


@app.post("/api/server/start")
async def api_server_start(req: ServerStartReq) -> Dict[str, Any]:
    if _STATE.server_proc is not None and _STATE.server_proc.poll() is None:
        raise HTTPException(400, "Server 已在运行；请先停止")
    csv_path = req.csv or _STATE.last_extract_csv or str(default_csv_path())
    if not Path(csv_path).exists():
        raise HTTPException(400, f"CSV 不存在: {csv_path}")

    py = _find_python_exe()
    server_py = _ROOT / "server.py"
    cmd = [py, str(server_py),
           "--host", req.host, "--port", str(req.port),
           "--csv", str(Path(csv_path).resolve()),
           "--ns-index", str(req.ns_index), "--ns-uri", req.ns_uri]
    if not req.occupancy_true:
        cmd.append("--no-occupancy-true")

    log.info("启动 Server: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ROOT), bufsize=0, env=_python_subprocess_env())
    _pipe_to_logger(proc, "server")
    _STATE.server_proc = proc
    await asyncio.sleep(0.3)  # 给一点时间抓早期错误
    return {"ok": True, "pid": proc.pid}


@app.post("/api/server/stop")
async def api_server_stop() -> Dict[str, Any]:
    return _stop_subprocess("server_proc")


class AgentStartReq(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4855
    config: Optional[str] = None      # 可选 yaml
    csv: Optional[str] = None


@app.post("/api/agent/start")
async def api_agent_start(req: AgentStartReq) -> Dict[str, Any]:
    if _STATE.agent_proc is not None and _STATE.agent_proc.poll() is None:
        raise HTTPException(400, "Handshake Agent 已在运行")
    py = _find_python_exe()
    agent_py = _ROOT / "handshake_agent.py"
    url = f"opc.tcp://{req.host}:{req.port}/xuse_sim/"
    cmd = [py, str(agent_py), "--url", url]
    if req.config:
        cmd.extend(["--config", req.config])
    csv_path = req.csv or _STATE.last_extract_csv or str(default_csv_path())
    if not Path(csv_path).exists():
        raise HTTPException(400, f"CSV 不存在: {csv_path}")
    cmd.extend(["--csv", csv_path])

    log.info("启动 Handshake Agent: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ROOT), bufsize=0, env=_python_subprocess_env())
    _pipe_to_logger(proc, "agent")
    _STATE.agent_proc = proc
    await asyncio.sleep(0.3)
    return {"ok": True, "pid": proc.pid}


@app.post("/api/agent/stop")
async def api_agent_stop() -> Dict[str, Any]:
    return _stop_subprocess("agent_proc")


# -- SSE 日志流 ------------------------------------------------------------
@app.get("/api/logs/stream")
async def api_logs_stream(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _STATE.log_queues.add(q)

    async def _gen():
        # 首帧: 心跳 + 当前状态
        yield f"event: state\ndata: {json.dumps(_STATE.snapshot())}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: log\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳，保持连接活着
                    yield ": ping\n\n"
                # 每次推日志后附带一次 state, 前端能低延迟看到 busy/pid 变化
                yield f"event: state\ndata: {json.dumps(_STATE.snapshot())}\n\n"
        finally:
            _STATE.log_queues.discard(q)

    return StreamingResponse(_gen(), media_type="text/event-stream")


# -- 一键流水线 -------------------------------------------------------------
class PipelineReq(BaseModel):
    include_all: bool = False
    ns_index: int = 4
    ns_prefix: str = "uniab|"
    node_language: str = "Chinese"
    expand_structs: bool = True
    host: str = "0.0.0.0"
    port: int = 4855
    also_start_agent: bool = False
    agent_config: Optional[str] = None


@app.post("/api/pipeline")
async def api_pipeline(req: PipelineReq) -> Dict[str, Any]:
    """extract → 启动 Server → (可选) 启动 Agent。"""
    # 1) extract (走已有 handler 的逻辑)
    ex_req = ExtractReq(include_all=req.include_all,
                        ns_index=req.ns_index, ns_prefix=req.ns_prefix,
                        node_language=req.node_language,
                        expand_structs=req.expand_structs,
                        preview_only=False)
    ex_result = await api_project_extract(ex_req)
    csv_path = ex_result["out_path"]
    # 2) server
    _stop_subprocess("server_proc")
    sv_req = ServerStartReq(csv=csv_path, host=req.host, port=req.port,
                            ns_index=req.ns_index)
    await api_server_start(sv_req)
    # 3) agent
    if req.also_start_agent:
        _stop_subprocess("agent_proc")
        ag_req = AgentStartReq(host="127.0.0.1", port=req.port,
                               config=req.agent_config, csv=csv_path)
        await api_agent_start(ag_req)
    return {"ok": True, "csv": csv_path, "state": _STATE.snapshot()}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description="OpcUaSim Web GUI 后端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    if not args.no_open:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
