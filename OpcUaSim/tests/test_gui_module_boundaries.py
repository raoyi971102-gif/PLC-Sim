from __future__ import annotations

import asyncio
from pathlib import Path

from gui.backend import _root, app

STATIC_DIRECTORY = Path(__file__).parents[1] / "gui" / "static"
SCRIPT_ORDER = (
    "app.js",
    "project.js",
    "simulation.js",
    "variables.js",
    "diagnostics.js",
)


def test_feature_routers_keep_public_http_paths() -> None:
    """拆分后的各路由模块继续发布原有 HTTP interface。"""

    route_paths = set(app.openapi()["paths"])
    assert {
        "/api/project/open",
        "/api/project/extract",
        "/api/server/start",
        "/api/server/variables",
        "/api/agent/start",
        "/api/agent/ptlc/fault",
        "/api/logs/stream",
    } <= route_paths


def test_frontend_scripts_load_in_dependency_order_with_cache_busting() -> None:
    """首页按依赖顺序加载全部脚本，并为每个文件独立生成版本参数。"""

    response = asyncio.run(_root())
    html = response.body.decode("utf-8")
    positions = [html.index(f"/static/{name}?v=") for name in SCRIPT_ORDER]

    assert positions == sorted(positions)


def test_frontend_features_live_in_their_own_script_modules() -> None:
    """核心脚本不再重新吸收工程、仿真、变量和诊断实现。"""

    core = (STATIC_DIRECTORY / "app.js").read_text(encoding="utf-8")
    features = {
        "project.js": "function buildExtractReq",
        "simulation.js": "function syncServerProfile",
        "variables.js": "function renderServerVariables",
        "diagnostics.js": "function connectSse",
    }

    for filename, marker in features.items():
        assert marker not in core
        assert marker in (STATIC_DIRECTORY / filename).read_text(encoding="utf-8")
