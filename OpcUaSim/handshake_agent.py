"""
XUSE 握手仿真代理（独立进程）
=======================================================================
职责 —— 作为 OPC UA Client 连到 server.py，仿真 PLC 端 4 类握手时序：
  Type-A 编码触发  (_动作触发 → _动作完成, 联动 _目标位置代码/_当前位置)
  Type-B 请求-应答 (_请求加工 / _开始加工 → _加工完成)
  Type-C 参数下发  (_参数下发 → _参数下发完成)
  Type-D 初始化    (_初始化   → _初始化完成)

支持通过 --config config.yaml 覆盖各握手默认耗时。
可只启动本代理来仿真握手（Server 必须先启动）。
可只启动 Server 而不启本代理（此时所有写入的 W 节点永远不会得到应答）。

用法：
    python handshake_agent.py                                # 连默认 endpoint 与 CSV
    python handshake_agent.py --url opc.tcp://127.0.0.1:4855/xuse_sim/
    python handshake_agent.py --csv my.csv --config config.yaml
    python handshake_agent.py --request-always-true          # 所有 _请求加工 常置 TRUE
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from opcua import Client, ua

from common import (
    NodeDef,
    default_csv_path,
    load_csvs,
    load_yaml,
    match_pos_node,
    parse_suffix,
    setup_logging,
)


log = setup_logging("XUSE-Agent")


# ---------------------------------------------------------------------------
# 握手实例
# ---------------------------------------------------------------------------
@dataclass
class Handshake:
    base: str                    # 共同前缀，如 "工站"
    kind: str                    # init_D / param_C / action_A / process_B
    write_node: Optional[Any] = None    # W 节点 (opcua.Node)
    read_node: Optional[Any] = None     # R 节点
    req_node: Optional[Any] = None      # REQ 节点 (仅 Type-B)
    delay_ms: int = 500
    active_task: Optional[threading.Thread] = None
    last_w: Any = False
    # Type-A 相关：目标位置代码 / 目标取放代码 → 当前位置 / 当前取放料
    target_pos_node: Optional[Any] = None
    target_pick_node: Optional[Any] = None
    current_pos_node: Optional[Any] = None
    current_pick_node: Optional[Any] = None


class HandshakeEngine:
    """
    以 tick 间隔轮询所有握手 W 节点，检测上升沿 → 启动仿真序列。
    每个握手一个短生命周期线程处理，避免相互阻塞。
    """

    def __init__(
        self,
        client: Client,
        node_defs: List[NodeDef],
        request_always_true: bool = False,
        tick_ms: int = 50,
        delays_override: Optional[Dict[str, int]] = None,
        init_req_true: bool = True,
    ):
        self.client = client
        self.tick_ms = tick_ms
        self.request_always_true = request_always_true
        self.init_req_true = init_req_true
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.handshakes: Dict[Tuple[str, str], Handshake] = {}
        self._active_lock = threading.Lock()
        delays_override = delays_override or {}

        # 通过 NodeId 获取节点对象
        nodes_by_cn: Dict[str, Any] = {}
        for nd in node_defs:
            try:
                nodes_by_cn[nd.name_cn] = client.get_node(nd.node_id)
            except Exception as e:
                log.warning("获取节点失败: %s (%s) — %s", nd.name_cn, nd.node_id, e)

        # 1) 分组组装 handshake 实例
        for cn, node in nodes_by_cn.items():
            kind_tuple = parse_suffix(cn)
            if not kind_tuple:
                continue
            base, role, kind, delay = kind_tuple
            key = (base, kind)
            hs = self.handshakes.setdefault(
                key,
                Handshake(base=base, kind=kind, delay_ms=delays_override.get(base, delay)),
            )
            if delay > 0 and (hs.delay_ms == 0 or hs.delay_ms < delay):
                hs.delay_ms = delays_override.get(base, delay)
            if role == "W":
                hs.write_node = node
                hs.last_w = self._safe_get(node)
            elif role == "R":
                hs.read_node = node
            elif role == "REQ":
                hs.req_node = node

        # 2) 附加 Type-A 的位置节点（同 base 前缀 + 相同通道号）
        for (base, kind), hs in self.handshakes.items():
            if kind != "action_A":
                continue
            for cn, node in nodes_by_cn.items():
                attr = match_pos_node(cn, base)
                if attr:
                    setattr(hs, attr, node)

        # 3) Type-B 初始态：置起所有 _请求加工 (模拟"物料已就位")
        if request_always_true:
            for hs in self.handshakes.values():
                if hs.req_node is not None:
                    self._safe_set(hs.req_node, True)
        elif init_req_true:
            for hs in self.handshakes.values():
                if hs.kind == "process_B" and hs.req_node is not None:
                    self._safe_set(hs.req_node, True)

        log.info("握手引擎装配完成，共识别握手实例：%d", len(self.handshakes))
        counts: Dict[str, int] = {}
        for hs in self.handshakes.values():
            counts[hs.kind] = counts.get(hs.kind, 0) + 1
        for k, v in sorted(counts.items()):
            log.info("  %s → %d 组", k, v)

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_get(node: Any) -> Any:
        try:
            return node.get_value()
        except Exception:
            return None

    @staticmethod
    def _safe_set(node: Any, value: Any) -> None:
        try:
            node.set_value(value)
        except Exception as e:
            log.warning("set_value 失败: %s (%s)", node, e)

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="HandshakeEngine", daemon=True)
        self._thread.start()
        log.info("握手引擎已启动 (tick=%d ms)", self.tick_ms)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        interval = self.tick_ms / 1000.0
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.exception("引擎 tick 异常: %s", e)
            time.sleep(interval)

    def _tick(self) -> None:
        for hs in self.handshakes.values():
            if hs.write_node is None or hs.read_node is None:
                continue
            cur = self._safe_get(hs.write_node)
            if cur is True and hs.last_w is not True:
                self._launch(hs)
            hs.last_w = cur

    def _launch(self, hs: Handshake) -> None:
        with self._active_lock:
            if hs.active_task and hs.active_task.is_alive():
                log.debug("握手 %s(%s) 已在运行，忽略重复触发", hs.base, hs.kind)
                return
            t = threading.Thread(
                target=self._execute, args=(hs,), name=f"HS-{hs.base}-{hs.kind}", daemon=True
            )
            hs.active_task = t
            t.start()

    def _execute(self, hs: Handshake) -> None:
        """
        统一时序：
          Step 1  预清 R
          Step 2  按类型执行副作用（Type-A 位置搬运）
          Step 3  sleep(delay_ms)
          Step 4  R = TRUE
          Step 5  等 W 变 FALSE
          Step 6  R = FALSE
          Type-B 额外：清 REQ + 稍后 REQ = TRUE
        """
        w = hs.write_node
        r = hs.read_node
        try:
            log.info("▶ 握手启动 %s [%s] — 预计 %d ms", hs.base, hs.kind, hs.delay_ms)
            self._safe_set(r, False)

            if hs.kind == "action_A":
                if hs.target_pos_node is not None and hs.current_pos_node is not None:
                    tgt = self._safe_get(hs.target_pos_node)
                    log.info("   位置搬运: %s 当前=%s → 目标=%s (延时结束后落位)",
                             hs.base, self._safe_get(hs.current_pos_node), tgt)

            time.sleep(hs.delay_ms / 1000.0)

            if hs.kind == "action_A":
                if hs.target_pos_node is not None and hs.current_pos_node is not None:
                    self._safe_set(hs.current_pos_node, self._safe_get(hs.target_pos_node))
                if hs.target_pick_node is not None and hs.current_pick_node is not None:
                    self._safe_set(hs.current_pick_node, self._safe_get(hs.target_pick_node))

            self._safe_set(r, True)
            log.info("✓ 完成置位 %s → %s = TRUE (等待上位机撤销 W)", hs.base, self._get_r_name(hs))

            if hs.kind == "process_B" and hs.req_node is not None and not self.request_always_true:
                self._safe_set(hs.req_node, False)

            # 等 W 撤销
            timeout_wait = 30.0
            t0 = time.time()
            while not self._stop.is_set():
                if self._safe_get(w) is not True:
                    break
                if time.time() - t0 > timeout_wait:
                    log.warning("⚠ %s 等待 W 撤销超时 %.0fs，强制回环", hs.base, timeout_wait)
                    break
                time.sleep(0.05)

            self._safe_set(r, False)

            if hs.kind == "process_B" and hs.req_node is not None and not self.request_always_true:
                time.sleep(0.3)
                self._safe_set(hs.req_node, True)

            log.info("↩ 握手回环 %s [%s] — R 已清零", hs.base, hs.kind)
        except Exception:
            log.exception("握手执行异常: %s(%s)", hs.base, hs.kind)

    @staticmethod
    def _get_r_name(hs: Handshake) -> str:
        try:
            return str(hs.read_node.get_browse_name().Name)
        except Exception:
            return "?"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def wait_server_ready(url: str, timeout: float = 15.0) -> Client:
    """带重试的连接：Server 启动稍慢时也能顺利连上。"""
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout:
        client = Client(url)
        try:
            client.connect()
            return client
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise ConnectionError(f"无法连接到 {url}（等待 {timeout:.0f}s）: {last_err}")


def main() -> int:
    parser = argparse.ArgumentParser(description="XUSE 握手仿真代理 (独立 OPC UA 客户端)")
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4855/xuse_sim/",
                        help="server.py 的 endpoint (默认 opc.tcp://127.0.0.1:4855/xuse_sim/)")
    default_csv = str(default_csv_path())
    parser.add_argument(
        "--csv",
        action="append",
        default=None,
        help=("CSV 变量表路径 (需与 Server 加载的表一致；可指定多次；默认: " + default_csv + ")"),
    )
    parser.add_argument("--tick-ms", type=int, default=50, help="握手引擎 tick 间隔 (ms)")
    parser.add_argument("--config", default=None, help="可选 YAML 配置：覆盖动作耗时等")
    parser.add_argument("--request-always-true", action="store_true",
                        help="强制所有 _请求加工 节点常置 TRUE (调试用)")
    parser.add_argument("--no-init-req-true", action="store_true",
                        help="禁用启动时把所有 _请求加工 置 TRUE 的初始化")
    parser.add_argument("--connect-timeout", type=float, default=15.0,
                        help="等待 Server 就绪的超时秒数 (默认 15s)")
    args = parser.parse_args()

    cfg = load_yaml(args.config)
    delays_override: Dict[str, int] = dict(cfg.get("delays", {}))

    csv_paths = [Path(p).resolve() for p in (args.csv or [default_csv])]
    for cp in csv_paths:
        if not cp.exists():
            log.error("CSV 文件不存在: %s", cp)
            return 2
    node_defs = load_csvs(csv_paths)

    log.info("正在连接 %s ...", args.url)
    try:
        client = wait_server_ready(args.url, timeout=args.connect_timeout)
    except ConnectionError as e:
        log.error(str(e))
        log.error("请确认 server.py 已经运行；示例: .\\start.bat")
        return 3
    log.info("✓ 已连接到 Server")

    engine: Optional[HandshakeEngine] = None
    try:
        engine = HandshakeEngine(
            client=client,
            node_defs=node_defs,
            request_always_true=args.request_always_true,
            tick_ms=args.tick_ms,
            delays_override=delays_override,
            init_req_true=not args.no_init_req_true,
        )
        engine.start()

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
        if engine is not None:
            engine.stop()
        try:
            client.disconnect()
            log.info("已断开与 Server 的连接。")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
