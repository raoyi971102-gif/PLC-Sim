"""PTLC V2 OPC UA / L2 握手仿真代理。

该进程只依赖 PLC-Sim 内置协议快照，不导入或修改 PTLC 仓库。它监视八个
``<Station>_L2_*`` 通道，模拟接单、运行、完成、复位以及少量可配置的设备副作用。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

try:
    from .common import load_yaml
except ImportError:  # Direct `python ptlc_handshake_agent.py` compatibility.
    from common import load_yaml


STATIONS = (
    "Sampling", "Collect", "Develop", "PhotoScrape",
    "FeedLift", "Pump", "Rail", "StagingA",
)
INPUT_FIELDS = ("ActionCode", "RequestSeq", "Start", "Reset")
OUTPUT_DEFAULTS: dict[str, Any] = {
    "State": 0,
    "ActiveCode": 0,
    "AcceptedSeq": 0,
    "CompletedSeq": 0,
    "Step": 0,
    "ErrorCode": 0,
    "SafeState": 0,
    "Retryable": False,
}
TERMINAL_STATES = {20, 30, 40, 50}


class VariableAdapter(Protocol):
    def read(self, name: str) -> Any: ...
    def write(self, name: str, value: Any) -> None: ...


class OpcUaVariableAdapter:
    """按 PTLC GVL BrowseName 路径定位变量并保持远端 VariantType 写入。"""

    def __init__(
        self,
        url: str,
        browse_path: tuple[str, ...],
        username: str = "",
        password: str = "",
    ) -> None:
        self.url = url
        self.browse_path = browse_path
        self.username = username
        self.password = password
        self._client = self._new_client()
        self._nodes: dict[str, Any] = {}
        self._gvl: Any = None

    def _new_client(self) -> Any:
        from opcua import Client

        client = Client(self.url, timeout=10)
        if self.username:
            client.set_user(self.username)
            client.set_password(self.password)
        return client

    def connect(self) -> None:
        self._client.connect()

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            pass

    def _reconnect(self) -> None:
        self.disconnect()
        self._client = self._new_client()
        self._client.connect()
        self._nodes.clear()
        self._gvl = None

    @staticmethod
    def _child(parent: Any, browse_name: str) -> Any:
        for child in parent.get_children():
            if child.get_browse_name().Name == browse_name:
                return child
        raise KeyError(f"BrowseName 子节点不存在: {browse_name}")

    def _gvl_node(self) -> Any:
        if self._gvl is None:
            node = self._client.get_objects_node()
            for part in self.browse_path:
                node = self._child(node, part)
            self._gvl = node
        return self._gvl

    def _node(self, name: str) -> Any:
        if name not in self._nodes:
            self._nodes[name] = self._child(self._gvl_node(), name)
        return self._nodes[name]

    def _io(self, name: str, operation: Any) -> Any:
        for attempt in range(3):
            try:
                return operation()
            except (TimeoutError, ConnectionError, OSError):
                if attempt == 2:
                    raise
                time.sleep(0.5)
                self._reconnect()
        raise AssertionError("unreachable")

    def read(self, name: str) -> Any:
        return self._io(name, lambda: self._node(name).get_value())

    def write(self, name: str, value: Any) -> None:
        self._io(name, lambda: self._write_once(name, value))

    def _write_once(self, name: str, value: Any) -> None:
        from opcua import ua

        node = self._node(name)
        variant_type = node.get_data_type_as_variant_type()
        node.set_value(ua.Variant(value, variant_type))


@dataclass(frozen=True)
class HandshakeEvent:
    station: str
    phase: str
    action_code: int
    request_seq: int


@dataclass
class _Cycle:
    action_code: int
    request_seq: int
    due_at: float
    outcome: str


class PtlcHandshakeSimulator:
    """同步、可注入时钟的 PTLC L2 状态机，便于单元测试和独立进程复用。"""

    def __init__(
        self,
        adapter: VariableAdapter,
        *,
        config: Mapping[str, Any] | None = None,
        delay_s: float = 0.2,
        stations: tuple[str, ...] = STATIONS,
    ) -> None:
        self.adapter = adapter
        self.config = dict(config or {})
        configured = tuple(str(item) for item in self.config.get("stations", stations))
        unknown = sorted(set(configured) - set(STATIONS))
        if unknown:
            raise ValueError(f"未知 PTLC L2 工位: {', '.join(unknown)}")
        self.stations = configured
        self.delay_s = max(float(delay_s), 0.0)
        self._previous_start = {station: False for station in self.stations}
        self._cycles: dict[str, _Cycle] = {}

    @staticmethod
    def node(station: str, field: str) -> str:
        return f"{station}_L2_{field}"

    def contract_names(self) -> tuple[str, ...]:
        names = {
            self.node(station, field)
            for station in self.stations
            for field in (*INPUT_FIELDS, *OUTPUT_DEFAULTS)
        }
        names.update(str(name) for name in self.config.get("initial_values", {}))
        names.update(("PLC_Ready", "PLC_Deploy_State"))
        for effect in self._all_effects():
            names.update(self._effect_names(effect))
        return tuple(sorted(names))

    def _all_effects(self) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        for effect in dict(self.config.get("station_effects", {})).values():
            if isinstance(effect, Mapping):
                result.append(effect)
        for by_action in dict(self.config.get("action_effects", {})).values():
            if isinstance(by_action, Mapping):
                for effect in by_action.values():
                    if isinstance(effect, Mapping):
                        result.append(effect)
        return result

    @staticmethod
    def _effect_names(effect: Mapping[str, Any]) -> set[str]:
        names = set(str(name) for name in dict(effect.get("set", {})))
        for item in effect.get("copy", ()) or ():
            names.update((str(item["from"]), str(item["to"])))
        for item in effect.get("indexed_copy", ()) or ():
            names.update((str(item["from"]), str(item["index"]), str(item["to"])))
        for item in effect.get("set_index", ()) or ():
            names.update((str(item["node"]), str(item["index"])))
        return names

    def initialize(self) -> None:
        for station in self.stations:
            for field, value in OUTPUT_DEFAULTS.items():
                self.adapter.write(self.node(station, field), value)
        for name, value in dict(self.config.get("initial_values", {})).items():
            self.adapter.write(str(name), value)

    def cleanup(self) -> None:
        self._cycles.clear()
        for station in self.stations:
            for field, value in OUTPUT_DEFAULTS.items():
                self.adapter.write(self.node(station, field), value)

    def check(self) -> list[str]:
        missing: list[str] = []
        for name in self.contract_names():
            try:
                self.adapter.read(name)
            except (KeyError, RuntimeError):
                missing.append(name)
        return missing

    def _fault_codes(self, station: str, key: str) -> set[int]:
        faults = dict(self.config.get("faults", {}))
        common = {int(v) for v in dict(faults.get("all", {})).get(key, ())}
        specific = {int(v) for v in dict(faults.get(station, {})).get(key, ())}
        return common | specific

    def _station_delay(self, station: str, code: int) -> float:
        action_delays = dict(self.config.get("action_delay_ms", {}))
        station_delays = dict(action_delays.get(station, {}))
        if str(code) in station_delays:
            return max(float(station_delays[str(code)]), 0.0) / 1000.0
        delays = dict(self.config.get("station_delay_ms", {}))
        if station in delays:
            return max(float(delays[station]), 0.0) / 1000.0
        return self.delay_s

    def _reset_station(self, station: str) -> None:
        self._cycles.pop(station, None)
        for field, value in OUTPUT_DEFAULTS.items():
            self.adapter.write(self.node(station, field), value)

    def _start_cycle(self, station: str, now: float) -> tuple[_Cycle, HandshakeEvent]:
        code = int(self.adapter.read(self.node(station, "ActionCode")))
        seq = int(self.adapter.read(self.node(station, "RequestSeq")))
        ready = bool(self.adapter.read("PLC_Ready"))
        deploy_state = int(self.adapter.read("PLC_Deploy_State"))
        if not ready or deploy_state != 0:
            outcome = "global_reject"
        elif code in self._fault_codes(station, "reject_codes"):
            outcome = "rejected"
        elif code in self._fault_codes(station, "error_codes"):
            outcome = "error"
        elif code in self._fault_codes(station, "hang_codes"):
            outcome = "hang"
        else:
            outcome = "done"
        cycle = _Cycle(code, seq, now + self._station_delay(station, code), outcome)
        self._cycles[station] = cycle
        self.adapter.write(self.node(station, "AcceptedSeq"), seq)
        self.adapter.write(self.node(station, "ActiveCode"), code)
        self.adapter.write(self.node(station, "Step"), 1)
        self.adapter.write(self.node(station, "State"), 10)
        return cycle, HandshakeEvent(station, "accepted", code, seq)

    def _effects_for(self, station: str, code: int) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        station_effect = dict(self.config.get("station_effects", {})).get(station)
        if isinstance(station_effect, Mapping):
            result.append(station_effect)
        action_effect = dict(
            dict(self.config.get("action_effects", {})).get(station, {})
        ).get(str(code))
        if isinstance(action_effect, Mapping):
            result.append(action_effect)
        return result

    def _apply_effect(self, effect: Mapping[str, Any]) -> None:
        for name, value in dict(effect.get("set", {})).items():
            self.adapter.write(str(name), value)
        for item in effect.get("copy", ()) or ():
            self.adapter.write(str(item["to"]), self.adapter.read(str(item["from"])))
        for item in effect.get("indexed_copy", ()) or ():
            values = list(self.adapter.read(str(item["from"])))
            index = int(self.adapter.read(str(item["index"]))) - int(item.get("index_base", 0))
            if not 0 <= index < len(values):
                raise IndexError(f"{item['from']} 索引越界: {index}")
            self.adapter.write(str(item["to"]), values[index])
        for item in effect.get("set_index", ()) or ():
            name = str(item["node"])
            values = list(self.adapter.read(name))
            index = int(self.adapter.read(str(item["index"]))) - int(item.get("index_base", 0))
            if not 0 <= index < len(values):
                raise IndexError(f"{name} 索引越界: {index}")
            values[index] = item.get("value")
            self.adapter.write(name, values)

    def _finish_cycle(self, station: str, cycle: _Cycle) -> HandshakeEvent:
        if cycle.outcome == "done":
            try:
                for effect in self._effects_for(station, cycle.action_code):
                    self._apply_effect(effect)
            except (KeyError, IndexError, TypeError, ValueError):
                state, error, safe, retryable, phase = 40, 500, 90, False, "error"
            else:
                state, error, safe, retryable, phase = 20, 0, 10, False, "completed"
        elif cycle.outcome == "global_reject":
            state, error, safe, retryable, phase = 30, 190, 0, True, "rejected"
        elif cycle.outcome == "rejected":
            state, error, safe, retryable, phase = 30, 102, 0, True, "rejected"
        else:
            state, error, safe, retryable, phase = 40, 201, 90, False, "error"
        self.adapter.write(self.node(station, "Step"), 90)
        self.adapter.write(self.node(station, "ErrorCode"), error)
        self.adapter.write(self.node(station, "SafeState"), safe)
        self.adapter.write(self.node(station, "Retryable"), retryable)
        self.adapter.write(self.node(station, "CompletedSeq"), cycle.request_seq)
        self.adapter.write(self.node(station, "State"), state)
        self._cycles.pop(station, None)
        return HandshakeEvent(station, phase, cycle.action_code, cycle.request_seq)

    def step(self, now: float | None = None) -> list[HandshakeEvent]:
        current = time.monotonic() if now is None else float(now)
        events: list[HandshakeEvent] = []
        for station in self.stations:
            reset = bool(self.adapter.read(self.node(station, "Reset")))
            start = bool(self.adapter.read(self.node(station, "Start")))
            state = int(self.adapter.read(self.node(station, "State")))
            if reset:
                if state != 0 or station in self._cycles:
                    old = self._cycles.get(station, _Cycle(0, 0, current, "done"))
                    self._reset_station(station)
                    events.append(HandshakeEvent(station, "reset", old.action_code, old.request_seq))
            elif start and not self._previous_start[station] and state == 0:
                cycle, accepted = self._start_cycle(station, current)
                events.append(accepted)
                if cycle.outcome != "hang" and cycle.due_at <= current:
                    events.append(self._finish_cycle(station, cycle))
            elif station in self._cycles:
                cycle = self._cycles[station]
                if cycle.outcome != "hang" and cycle.due_at <= current:
                    events.append(self._finish_cycle(station, cycle))
            elif not start and state in TERMINAL_STATES:
                self.adapter.write(self.node(station, "State"), 0)
                self.adapter.write(self.node(station, "Step"), 0)
                events.append(HandshakeEvent(
                    station, "rearmed",
                    int(self.adapter.read(self.node(station, "ActiveCode"))),
                    int(self.adapter.read(self.node(station, "CompletedSeq"))),
                ))
            self._previous_start[station] = start
        return events


def _config_path() -> str:
    return str(Path(__file__).with_name("config") / "ptlc_handshake.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("list", "check", "serve"), default="serve")
    parser.add_argument("--config", default=_config_path())
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4855/xuse_sim/")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--delay-ms", type=int, default=None)
    parser.add_argument("--poll-ms", type=int, default=None)
    parser.add_argument("--max-actions", type=int, default=0)
    parser.add_argument("--no-initialize", action="store_true")
    parser.add_argument("--keep-state-on-exit", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_yaml(args.config)
    browse_path = tuple(str(part) for part in config.get("gvl_path", ()))
    if not browse_path:
        print("PTLC 配置缺少 gvl_path", file=sys.stderr)
        return 2
    delay_s = max(float(
        args.delay_ms if args.delay_ms is not None else config.get("delay_ms", 200)
    ), 0.0) / 1000.0
    poll_s = max(float(
        args.poll_ms if args.poll_ms is not None else config.get("poll_ms", 20)
    ), 5.0) / 1000.0
    adapter = OpcUaVariableAdapter(
        args.url, browse_path, username=args.username, password=args.password
    )
    simulator = PtlcHandshakeSimulator(adapter, config=config, delay_s=delay_s)

    if args.command == "list":
        print(json.dumps({
            "stations": simulator.stations,
            "nodes": simulator.contract_names(),
        }, ensure_ascii=False, indent=2))
        return 0

    adapter.connect()
    try:
        if args.command == "check":
            missing = simulator.check()
            if missing:
                print("缺少 PTLC 协议节点：\n" + "\n".join(missing), file=sys.stderr)
                return 1
            print(f"PTLC 协议检查通过：{len(simulator.contract_names())} 个节点")
            return 0

        if not args.no_initialize:
            simulator.initialize()
        stopping = False

        def _stop(signum: int, frame: Any) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, _stop)
        try:
            signal.signal(signal.SIGTERM, _stop)
        except (AttributeError, ValueError):
            pass

        completed = 0
        print(f"PTLC L2 代理已连接 {args.url}，工位={','.join(simulator.stations)}", flush=True)
        while not stopping:
            for event in simulator.step():
                print(json.dumps(event.__dict__, ensure_ascii=False), flush=True)
                if event.phase in {"completed", "rejected", "error"}:
                    completed += 1
            if args.max_actions and completed >= args.max_actions:
                break
            time.sleep(poll_s)
    finally:
        if args.command == "serve" and not args.keep_state_on_exit:
            try:
                simulator.cleanup()
            except Exception as exc:
                print(f"PTLC 代理清理状态失败: {exc}", file=sys.stderr)
        adapter.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
