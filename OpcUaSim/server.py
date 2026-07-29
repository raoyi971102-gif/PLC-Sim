"""
XUSE OPC UA 仿真服务器（纯服务器版）
=======================================================================
职责 —— 只做 OPC UA 服务：
  1. 从 CSV 批量创建变量节点，NodeId 严格保持 ns=4;s=uniab|<中文名>
  2. 允许匿名接入（NoSecurity），驱动一键连上
  3. 可选把 "_占位 / _空闲" 类节点初值设为 TRUE，便于握手代理跑 Type-B

握手仿真已拆到独立进程 handshake_agent.py，按需另行启动。

用法：
    python server.py                             # 默认参数启动
    python server.py --port 4855 --csv my.csv
    python server.py --csv a.csv --csv b.csv     # 合并多份 CSV
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List

from opcua import Server, ua

from common import (
    DEFAULT_MAP,
    VTYPE_MAP,
    NodeDef,
    OCC_RE,
    default_csv_path,
    load_csvs,
    setup_logging,
)


log = setup_logging("XUSE-Server")


# ---------------------------------------------------------------------------
# OPC UA Server 构建
# ---------------------------------------------------------------------------
def build_server(endpoint: str) -> Server:
    server = Server()
    server.set_endpoint(endpoint)
    server.set_server_name("XUSE Simulation OPC UA Server")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    return server


def register_ns_padding(server: Server, target_index: int, xuse_uri: str) -> int:
    """确保 xuse URI 命名空间索引 == target_index（默认 4）。"""
    log.info("初始命名空间: %s", server.get_namespace_array())
    while len(server.get_namespace_array()) < target_index:
        placeholder = f"urn:xuse:sim:placeholder:{len(server.get_namespace_array())}"
        idx = server.register_namespace(placeholder)
        log.debug("占位命名空间 %s → ns=%d", placeholder, idx)
    idx = server.register_namespace(xuse_uri)
    log.info("XUSE 命名空间 %s → ns=%d", xuse_uri, idx)
    assert idx == target_index, f"命名空间索引不匹配: 期望 {target_index}, 实际 {idx}"
    return idx


def add_nodes(server: Server, ns_idx: int, defs: List[NodeDef]) -> Dict[str, Any]:
    """把 CSV 中的每个变量作为 Objects 下的直接子节点创建。"""
    objects = server.get_objects_node()
    result: Dict[str, Any] = {}
    for nd in defs:
        variant = VTYPE_MAP[nd.data_type]
        default = DEFAULT_MAP[nd.data_type]
        try:
            nid = ua.NodeId.from_string(nd.node_id)
        except Exception:
            s_part = nd.node_id.split("s=", 1)[-1]
            nid = ua.NodeId(s_part, ns_idx, ua.NodeIdType.String)

        if nid.NamespaceIndex != ns_idx:
            log.warning("CSV NodeId ns=%d 与目标 ns=%d 不一致 (%s)，已改写",
                        nid.NamespaceIndex, ns_idx, nd.name_cn)
            nid = ua.NodeId(nid.Identifier, ns_idx, nid.NodeIdType)

        var = objects.add_variable(nid, nd.name_cn, default, varianttype=variant)
        var.set_writable()
        if nd.name_en:
            try:
                var.set_attribute(
                    ua.AttributeIds.Description,
                    ua.DataValue(ua.Variant(ua.LocalizedText(nd.name_en), ua.VariantType.LocalizedText)),
                )
            except Exception:
                pass
        result[nd.name_cn] = var
    log.info("已创建 %d 个变量节点 (ns=%d)", len(result), ns_idx)
    return result


def set_initial_occupancy(nodes_by_cn: Dict[str, Any], enable: bool) -> int:
    """把 "_占位 / _空闲" 类节点初值置 TRUE，方便 Type-B 握手代理直接跑。"""
    if not enable:
        return 0
    count = 0
    for cn, node in nodes_by_cn.items():
        if OCC_RE.search(cn):
            try:
                node.set_value(True)
                count += 1
            except Exception:
                pass
    log.info("已把 %d 个 '_占位/_空闲' 节点初值置 TRUE", count)
    return count


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="XUSE OPC UA 仿真服务器（纯服务器版）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=4855, help="监听端口 (默认 4855)")
    default_csv = str(default_csv_path())
    parser.add_argument(
        "--csv",
        action="append",
        default=None,
        help=("CSV 变量表路径 (可指定多次以加载多份 CSV；默认: " + default_csv + ")"),
    )
    parser.add_argument("--ns-uri", default="urn:xuse:sim", help="XUSE 命名空间 URI")
    parser.add_argument("--ns-index", type=int, default=4, help="XUSE 命名空间索引 (默认 4)")
    parser.add_argument("--no-occupancy-true", action="store_true",
                        help="禁用 '_占位 / _空闲' 节点初值默认 TRUE")
    args = parser.parse_args()

    csv_paths = [Path(p).resolve() for p in (args.csv or [default_csv])]
    for cp in csv_paths:
        if not cp.exists():
            log.error("CSV 文件不存在: %s", cp)
            return 2
    log.info("将加载 %d 份 CSV：", len(csv_paths))
    for cp in csv_paths:
        log.info("  - %s", cp)

    node_defs: List[NodeDef] = load_csvs(csv_paths)

    endpoint = f"opc.tcp://{args.host}:{args.port}/xuse_sim/"
    server = build_server(endpoint)
    ns_idx = register_ns_padding(server, args.ns_index, args.ns_uri)

    server.start()
    log.info("=" * 68)
    log.info("OPC UA 服务器已启动")
    log.info("  Endpoint : %s", endpoint)
    log.info("  Namespace: ns=%d (%s)", ns_idx, args.ns_uri)
    log.info("  Anon     : 允许匿名 (NoSecurity)")
    log.info("  Handshake: 未启动 (如需请另开进程运行 handshake_agent.py)")
    log.info("=" * 68)

    try:
        nodes_by_cn = add_nodes(server, ns_idx, node_defs)
        set_initial_occupancy(nodes_by_cn, enable=not args.no_occupancy_true)

        stop_evt = threading.Event()

        def _handler(signum, frame):
            log.info("收到信号 %s，退出…", signum)
            stop_evt.set()

        signal.signal(signal.SIGINT, _handler)
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (AttributeError, ValueError):
            pass

        while not stop_evt.is_set():
            stop_evt.wait(timeout=1.0)
    finally:
        server.stop()
        log.info("服务器已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
