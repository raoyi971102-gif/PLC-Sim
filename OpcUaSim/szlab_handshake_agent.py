"""
SZLab Poly Studio 握手仿真驱动。

本进程作为 OPC UA Client 连接已有仿真 Server，按 Uni-Lab-OS 中 SZLab
设备驱动的实际契约模拟 PLC 侧响应：

- Robot：任务写入完成 -> 任务完成（返回任务号），并管理允许写入。
- S04：六个磁搅位参数写入完成 -> 加工完成。
- S06：加液参数写入完成 -> 加工完成。
- S07/S08/S09：参数写入完成 -> 工艺完成（返回工艺选择值）。
- S05：启动时提供拍照完成和 OK 结果。

节点优先按实机 NodeId ``ns=4;s=上位机通讯|<变量名>`` 直连；直连失败时
递归扫描 BrowseName，因此也能连接由本仓库或 Uni-LabOS 测试工具创建的服务器。
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from opcua import Client, ua

from common import load_yaml, setup_logging


log = setup_logging("SZLab-Handshake")


@dataclass(frozen=True)
class HandshakeRule:
    key: str
    trigger: str
    completion: str
    selector: str
    result_kind: str  # bool / selector / robot
    group: str
    ready_node: Optional[str] = None


@dataclass
class RuleState:
    previous_trigger: Any = False
    active: bool = False
    completed: bool = False
    due_at: Optional[float] = None


def build_rules() -> list[HandshakeRule]:
    rules = [
        HandshakeRule(
            key="robot",
            trigger="Robot_任务写入完成",
            completion="Robot_任务完成",
            selector="任务号",
            result_kind="robot",
            group="robot",
            ready_node="Robot_任务允许写入",
        )
    ]
    rules.extend(
        HandshakeRule(
            key=f"s04:{position}",
            trigger=f"S04{position}参数写入完成",
            completion=f"S04{position}加工完成",
            selector=f"S04{position}磁搅工艺选择",
            result_kind="bool",
            group="s04",
        )
        for position in range(1, 7)
    )
    rules.extend(
        [
            HandshakeRule(
                key="s06",
                trigger="S06参数写入完成",
                completion="S06加工完成",
                selector="S06工艺选择",
                result_kind="bool",
                group="s06",
            ),
            HandshakeRule(
                key="s07",
                trigger="S07参数写入完成",
                completion="S07工艺完成",
                selector="S07工艺选择",
                result_kind="selector",
                group="s07",
            ),
            HandshakeRule(
                key="s08",
                trigger="S08参数写入完成",
                completion="S08工艺完成",
                selector="S08工艺选择",
                result_kind="selector",
                group="s08",
            ),
            HandshakeRule(
                key="s09",
                trigger="S09参数写入完成",
                completion="S09工艺完成",
                selector="S09工艺选择",
                result_kind="selector",
                group="s09",
            ),
        ]
    )
    return rules


def default_initial_values() -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "Robot_Home": True,
        "Robot_任务允许写入": True,
        "Robot_任务完成": 0,
        "S05准备信号": True,
        "S05加工完成": True,
        "S05拍照结果": 1,
        "S06准备信号": True,
        "S06允许加工": True,
        "S06加工完成": False,
        "S07原点信号": True,
        "S07允许加工": True,
        "S07工艺完成": 0,
        "S08原点信号": True,
        "S08允许加工": True,
        "S08工艺完成": 0,
        "S09允许加工": True,
        "S09天平读数稳定": True,
        "S09天平读数": 12.34,
        "S09工艺完成": 0,
    }
    for position in range(1, 7):
        values[f"S04{position}准备信号"] = True
        values[f"S04{position}允许加工"] = True
        values[f"S04{position}加工完成"] = False
    for position in range(1, 5):
        values[f"S09原点信号_{position}"] = True
    return values


class SzlabHandshakeSimulator:
    def __init__(
        self,
        url: str,
        *,
        namespace_index: int = 4,
        node_prefix: str = "上位机通讯|",
        delay_ms: int = 120,
        poll_ms: int = 20,
        delays: Optional[Dict[str, int]] = None,
        initial_values: Optional[Dict[str, Any]] = None,
        strict: bool = False,
        client: Optional[Client] = None,
    ) -> None:
        self.url = url
        self.namespace_index = int(namespace_index)
        self.node_prefix = node_prefix
        self.delay_ms = max(0, int(delay_ms))
        self.poll_ms = max(5, int(poll_ms))
        self.delays = {str(key): int(value) for key, value in (delays or {}).items()}
        self.initial_values = {**default_initial_values(), **dict(initial_values or {})}
        self.strict = strict
        self.client = client or Client(url, timeout=4)
        self.nodes: Dict[str, Any] = {}
        self.rules = build_rules()
        self.enabled_rules: list[HandshakeRule] = []
        self.states: Dict[str, RuleState] = {}
        self._stop = threading.Event()
        self._connected = False

    @property
    def required_names(self) -> set[str]:
        names = set(self.initial_values)
        for rule in self.rules:
            names.update((rule.trigger, rule.completion, rule.selector))
            if rule.ready_node:
                names.add(rule.ready_node)
        return names

    def connect(self, timeout: float = 15.0) -> None:
        started_at = time.monotonic()
        last_error: Optional[Exception] = None
        while time.monotonic() - started_at < timeout:
            try:
                self.client.connect()
                self._connected = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.5)
        if not self._connected:
            raise ConnectionError(f"无法连接到 {self.url}: {last_error}")

        self._resolve_nodes()
        self._prepare_rules()
        log.info(
            "已连接 %s，解析节点 %d 个，启用握手 %d 组",
            self.url,
            len(self.nodes),
            len(self.enabled_rules),
        )

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self.client.disconnect()
        finally:
            self._connected = False

    def _resolve_nodes(self) -> None:
        unresolved = set(self.required_names)
        for name in list(unresolved):
            node_id = f"ns={self.namespace_index};s={self.node_prefix}{name}"
            node = self.client.get_node(node_id)
            try:
                node.get_data_type_as_variant_type()
            except Exception:
                continue
            self.nodes[name] = node
            unresolved.discard(name)

        if unresolved:
            browse_index = self._browse_name_index()
            for name in unresolved:
                node = browse_index.get(name)
                if node is not None:
                    self.nodes[name] = node

    def _browse_name_index(self, max_depth: int = 12, max_nodes: int = 15000) -> Dict[str, Any]:
        index: Dict[str, Any] = {}
        stack: list[tuple[Any, int]] = [(self.client.get_objects_node(), 0)]
        visited = 0
        while stack and visited < max_nodes:
            node, depth = stack.pop()
            visited += 1
            try:
                browse_name = node.get_browse_name().Name
                index.setdefault(browse_name, node)
                if depth < max_depth:
                    stack.extend((child, depth + 1) for child in node.get_children())
            except Exception:
                continue
        log.info("BrowseName 回退扫描完成：visited=%d indexed=%d", visited, len(index))
        return index

    def _prepare_rules(self) -> None:
        self.enabled_rules.clear()
        missing_by_rule: Dict[str, list[str]] = {}
        for rule in self.rules:
            needed = [rule.trigger, rule.completion, rule.selector]
            if rule.ready_node:
                needed.append(rule.ready_node)
            missing = [name for name in needed if name not in self.nodes]
            if missing:
                missing_by_rule[rule.key] = missing
                continue
            self.enabled_rules.append(rule)
            self.states[rule.key] = RuleState(
                previous_trigger=bool(self._read(rule.trigger))
            )

        if missing_by_rule:
            details = "; ".join(
                f"{key}: {', '.join(names)}" for key, names in missing_by_rule.items()
            )
            if self.strict:
                raise RuntimeError(f"SZLab 握手节点不完整：{details}")
            log.warning("部分握手因 CSV/Server 缺少节点而跳过：%s", details)

    def initialize(self) -> None:
        written = 0
        for name, value in self.initial_values.items():
            if name not in self.nodes:
                continue
            self._write(name, value)
            written += 1
        for rule in self.enabled_rules:
            self.states[rule.key].previous_trigger = bool(self._read(rule.trigger))
        log.info("PLC 侧握手初始状态已写入：%d 个节点", written)

    def run_forever(self, initialize: bool = True) -> None:
        if initialize:
            self.initialize()
        log.info("SZLab 握手仿真已启动，poll=%dms", self.poll_ms)
        while not self._stop.wait(self.poll_ms / 1000.0):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("握手轮询异常：%s", exc)

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        for rule in self.enabled_rules:
            self._tick_rule(rule, self.states[rule.key], now)

    def _tick_rule(self, rule: HandshakeRule, state: RuleState, now: float) -> None:
        trigger = bool(self._read(rule.trigger))
        selector = self._read(rule.selector)

        if trigger and not bool(state.previous_trigger) and not state.active:
            if not bool(selector):
                log.warning("忽略 %s 触发：%s 仍为 %r", rule.key, rule.selector, selector)
            else:
                state.active = True
                state.completed = False
                state.due_at = now + self._rule_delay_ms(rule) / 1000.0
                self._write(rule.completion, self._neutral_value(rule))
                if rule.ready_node:
                    self._write(rule.ready_node, False)
                log.info(
                    "握手触发 %s：%s=%r，预计 %dms 后完成",
                    rule.key,
                    rule.selector,
                    selector,
                    self._rule_delay_ms(rule),
                )

        if state.active and not state.completed and state.due_at is not None and now >= state.due_at:
            selector = self._read(rule.selector)
            result = self._result_value(rule, selector)
            self._write(rule.completion, result)
            state.completed = True
            log.info("握手完成 %s：%s=%r", rule.key, rule.completion, result)

        # S09 的 trigger 是短脉冲，不能在下降沿立刻清完成；等上位机读取完成并
        # 把工艺选择/任务号复位为 0 后，再清 PLC 侧完成信号。
        if state.active and not trigger and not bool(selector):
            self._write(rule.completion, self._neutral_value(rule))
            if rule.ready_node:
                self._write(rule.ready_node, True)
            state.active = False
            state.completed = False
            state.due_at = None
            log.info("握手回环 %s：完成信号已复位", rule.key)

        state.previous_trigger = trigger

    def _rule_delay_ms(self, rule: HandshakeRule) -> int:
        return max(
            0,
            int(self.delays.get(rule.key, self.delays.get(rule.group, self.delay_ms))),
        )

    @staticmethod
    def _neutral_value(rule: HandshakeRule) -> Any:
        return False if rule.result_kind == "bool" else 0

    @staticmethod
    def _result_value(rule: HandshakeRule, selector: Any) -> Any:
        if rule.result_kind == "bool":
            return True
        value = int(selector or 0)
        return value if value != 0 else 1

    def _read(self, name: str) -> Any:
        return self.nodes[name].get_value()

    def _write(self, name: str, value: Any) -> None:
        node = self.nodes[name]
        try:
            variant_type = node.get_data_type_as_variant_type()
            typed = self._coerce_for_variant(value, variant_type)
            node.set_value(ua.Variant(typed, variant_type))
        except AttributeError:
            # 测试替身只需实现 get_value/set_value。
            node.set_value(value)

    @staticmethod
    def _coerce_for_variant(value: Any, variant_type: ua.VariantType) -> Any:
        if variant_type == ua.VariantType.Boolean:
            return bool(value)
        if variant_type in {
            ua.VariantType.Byte,
            ua.VariantType.SByte,
            ua.VariantType.Int16,
            ua.VariantType.UInt16,
            ua.VariantType.Int32,
            ua.VariantType.UInt32,
            ua.VariantType.Int64,
            ua.VariantType.UInt64,
        }:
            return int(value)
        if variant_type in {ua.VariantType.Float, ua.VariantType.Double}:
            return float(value)
        if variant_type == ua.VariantType.String:
            return str(value)
        return value


def _config_path() -> str:
    return str(Path(__file__).with_name("config") / "szlab_handshake.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SZLab Poly Studio OPC UA 握手仿真驱动")
    parser.add_argument(
        "--url",
        default="opc.tcp://127.0.0.1:4855/xuse_sim/",
        help="OPC UA Server endpoint",
    )
    parser.add_argument("--config", default=_config_path(), help="YAML 配置文件")
    parser.add_argument("--namespace-index", type=int, default=4)
    parser.add_argument("--node-prefix", default="上位机通讯|")
    parser.add_argument("--delay-ms", type=int, default=None)
    parser.add_argument("--poll-ms", type=int, default=None)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--strict", action="store_true", help="缺少任一握手节点时退出")
    parser.add_argument("--no-initialize", action="store_true", help="不写入 PLC 侧初始状态")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(args.config)
    simulator = SzlabHandshakeSimulator(
        url=args.url,
        namespace_index=args.namespace_index,
        node_prefix=args.node_prefix,
        delay_ms=args.delay_ms if args.delay_ms is not None else int(config.get("delay_ms", 120)),
        poll_ms=args.poll_ms if args.poll_ms is not None else int(config.get("poll_ms", 20)),
        delays=dict(config.get("delays", {})),
        initial_values=dict(config.get("initial_values", {})),
        strict=args.strict,
    )

    def _request_stop(signum: int, frame: Any) -> None:
        del frame
        log.info("收到信号 %s，正在停止", signum)
        simulator.stop()

    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (AttributeError, ValueError):
        pass

    try:
        simulator.connect(timeout=args.connect_timeout)
        simulator.run_forever(initialize=not args.no_initialize)
    except (ConnectionError, RuntimeError) as exc:
        log.error("%s", exc)
        return 2
    finally:
        simulator.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
