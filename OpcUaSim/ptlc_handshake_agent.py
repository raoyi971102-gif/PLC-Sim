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
    from .ptlc_behavior import StationContract, load_behavior_contracts
except ImportError:  # Direct `python ptlc_handshake_agent.py` compatibility.
    from common import load_yaml
    from ptlc_behavior import StationContract, load_behavior_contracts


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
    started_at: float
    due_at: float
    outcome: str
    error_code: int = 0
    safe_state: int = 10
    retryable: bool = False
    steps: tuple[int, ...] = ()
    last_step_index: int = -1
    motion: tuple[tuple[str, str, float, float], ...] = ()


@dataclass
class _DeployCycle:
    request_seq: int
    preparing_since: float


class RuntimeFaults:
    """可在运行期替换的确定性故障表。"""

    VALID_OUTCOMES = frozenset({"done", "reject", "error", "hang", "interrupt"})

    def __init__(self) -> None:
        self._items: dict[tuple[str, int], str] = {}

    def set(self, station: str, action_code: int, outcome: str) -> None:
        normalized = str(outcome).strip().lower()
        if normalized not in self.VALID_OUTCOMES:
            raise ValueError(f"未知故障结果: {outcome}")
        self._items[(station, int(action_code))] = normalized

    def clear(self, station: str | None = None, action_code: int | None = None) -> None:
        if station is None and action_code is None:
            self._items.clear()
            return
        for key in list(self._items):
            if (station is None or key[0] == station) and (
                action_code is None or key[1] == int(action_code)
            ):
                self._items.pop(key, None)

    def outcome(self, station: str, action_code: int) -> str | None:
        return self._items.get((station, int(action_code)))

    def load_payload(self, payload: Mapping[str, Any]) -> None:
        self._items.clear()
        for station, by_code in payload.items():
            if station not in STATIONS or not isinstance(by_code, Mapping):
                continue
            for code, outcome in by_code.items():
                self.set(station, int(code), str(outcome))

    def snapshot(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for (station, code), outcome in sorted(self._items.items()):
            result.setdefault(station, {})[str(code)] = outcome
        return result


class PtlcHandshakeSimulator:
    """同步、可注入时钟的 PTLC L2 状态机，便于单元测试和独立进程复用。"""

    def __init__(
        self,
        adapter: VariableAdapter,
        *,
        config: Mapping[str, Any] | None = None,
        delay_s: float = 0.2,
        stations: tuple[str, ...] = STATIONS,
        contracts: Mapping[str, StationContract] | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = dict(config or {})
        configured = tuple(str(item) for item in self.config.get("stations", stations))
        unknown = sorted(set(configured) - set(STATIONS))
        if unknown:
            raise ValueError(f"未知 PTLC L2 工位: {', '.join(unknown)}")
        self.stations = configured
        self.delay_s = max(float(delay_s), 0.0)
        self.contracts = dict(contracts or load_behavior_contracts())
        missing_contracts = sorted(set(self.stations) - set(self.contracts))
        if missing_contracts:
            raise ValueError(f"缺少 PTLC 工位行为契约: {', '.join(missing_contracts)}")
        self._previous_start = {station: False for station in self.stations}
        self._cycles: dict[str, _Cycle] = {}
        self._previous_deploy_start = False
        self._deploy_cycle: _DeployCycle | None = None
        self.runtime_faults = RuntimeFaults()
        process = dict(self.config.get("process", {}))
        material = dict(process.get("material", {}))
        self.process_state: dict[str, Any] = {
            "feed_count": max(0, int(material.get("feed_count", 12))),
            "waste_count": max(0, int(material.get("waste_count", 0))),
            "waste_armed": False,
            "vacuum_on": False,
        }
        self.events: list[dict[str, Any]] = []

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
        names.update((
            "PLC_Ready", "PLC_Axis_CommOperational",
            "PLC_Deploy_RequestSeq", "PLC_Deploy_CommitSeq",
            "PLC_Deploy_Start", "PLC_Deploy_Reset", "PLC_Deploy_State",
            "PLC_Deploy_AcceptedSeq", "PLC_Deploy_ErrorCode",
            "Pump_Vacuum_On",
        ))
        for effect in self._all_effects():
            names.update(self._effect_names(effect))
        for items in dict(self.config.get("motion_effects", {})).values():
            for item in items or ():
                if isinstance(item, Mapping):
                    names.update((str(item.get("from", "")), str(item.get("to", ""))))
        names.discard("")
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
                try:
                    self.adapter.write(self.node(station, field), value)
                except KeyError:
                    # 参考 PLC 的最小耦合原则：缺少可选/漂移节点只降级，不让代理整体退出。
                    continue
        for name, value in dict(self.config.get("initial_values", {})).items():
            try:
                self.adapter.write(str(name), value)
            except KeyError:
                continue

    def cleanup(self) -> None:
        self._cycles.clear()
        for station in self.stations:
            for field, value in OUTPUT_DEFAULTS.items():
                try:
                    self.adapter.write(self.node(station, field), value)
                except KeyError:
                    continue
        try:
            self.adapter.write("Pump_Vacuum_On", False)
            self.process_state["vacuum_on"] = False
        except KeyError:
            pass
        try:
            enables = list(self.adapter.read("Tank_Drain_Enable"))
            self.adapter.write("Tank_Drain_Enable", [False] * len(enables))
        except (KeyError, TypeError):
            pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_cycles": {
                station: {
                    "action_code": cycle.action_code,
                    "request_seq": cycle.request_seq,
                    "outcome": cycle.outcome,
                }
                for station, cycle in self._cycles.items()
            },
            "deploy_active": self._deploy_cycle is not None,
            "process": dict(self.process_state),
            "faults": self.runtime_faults.snapshot(),
            "events": self.events[-200:],
        }

    def _record(self, event: HandshakeEvent) -> None:
        self.events.append({
            "station": event.station,
            "phase": event.phase,
            "action_code": event.action_code,
            "request_seq": event.request_seq,
        })
        if len(self.events) > 1000:
            del self.events[:-500]

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

    def _motion_for(self, station: str) -> tuple[tuple[str, str, float, float], ...]:
        """捕获连续运动的 (target, actual, start, target_value)。"""
        result: list[tuple[str, str, float, float]] = []
        for item in dict(self.config.get("motion_effects", {})).get(station, ()) or ():
            try:
                target_name = str(item["from"])
                actual_name = str(item["to"])
                result.append((
                    target_name,
                    actual_name,
                    float(self.adapter.read(actual_name)),
                    float(self.adapter.read(target_name)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(result)

    def _motion_duration(
        self,
        station: str,
        motion: tuple[tuple[str, str, float, float], ...],
    ) -> float:
        speed = max(float(dict(self.config.get("motion_speed", {})).get(station, 100.0)), 0.001)
        return max((abs(target - start) / speed for _, _, start, target in motion), default=0.0)

    def _validate_action(self, station: str, code: int) -> tuple[str, int, int, bool] | None:
        """复刻能由 flat OPC UA 参数确定的受理门；返回终态近似或 None。"""
        try:
            if station == "Rail" and code == 10:
                position = int(self.adapter.read("Rail_Target_Position"))
                if not 1 <= position <= 6:
                    return "rejected", 101, 0, True
                targets = list(self.adapter.read("Rail_Pos_Target"))
                target = float(targets[position - 1])
                if not 0.0 < target <= 3000.0:
                    return "rejected", 102, 0, True
            elif station == "Sampling" and code == 55:
                count = int(self.adapter.read("Sampling_rinse_mix_count"))
                instructions = list(self.adapter.read("Sampling_rinse_mix_instructions"))
                if not 1 <= count <= 20 or len(instructions) != 4 or not all(
                    str(value).strip() for value in instructions
                ):
                    return "error", 466, 90, False
            elif station == "FeedLift" and code == 91:
                if int(self.adapter.read("FeedLift_DebugAxis")) not in {1, 2}:
                    return "error", 306, 90, False
        except (KeyError, IndexError, TypeError, ValueError):
            # 旧快照缺参数时由 contract check 报漂移，运行路径保持可降级。
            return None
        return None

    def _reset_station(self, station: str) -> None:
        self._cycles.pop(station, None)
        for field, value in OUTPUT_DEFAULTS.items():
            self.adapter.write(self.node(station, field), value)

    def _start_cycle(self, station: str, now: float) -> tuple[_Cycle, HandshakeEvent]:
        code = int(self.adapter.read(self.node(station, "ActionCode")))
        seq = int(self.adapter.read(self.node(station, "RequestSeq")))
        ready = bool(self.adapter.read("PLC_Ready"))
        deploy_state = int(self.adapter.read("PLC_Deploy_State"))
        contract = self.contracts[station]
        runtime_outcome = self.runtime_faults.outcome(station, code)
        error_code = 0
        safe_state = 10
        retryable = False
        if not ready or deploy_state != 0:
            outcome, error_code, safe_state, retryable = "global_reject", 190, 0, True
        elif code not in contract.accepts:
            outcome = "unknown"
            error_code, safe_state, retryable = contract.unknown_code_error, 0, True
        elif runtime_outcome:
            outcome = runtime_outcome
        elif (validation := self._validate_action(station, code)) is not None:
            outcome, error_code, safe_state, retryable = validation
        elif code in self._fault_codes(station, "reject_codes"):
            outcome = "rejected"
        elif code in self._fault_codes(station, "error_codes"):
            outcome = "error"
        elif code in self._fault_codes(station, "hang_codes"):
            outcome = "hang"
        else:
            outcome = "done"
        if outcome in {"rejected", "reject"}:
            error_code, safe_state, retryable = error_code or 102, 0, True
        elif outcome == "error":
            error_code, safe_state = 201, 90
        elif outcome == "interrupt":
            error_code, safe_state = 202, 90
        motion = self._motion_for(station) if outcome == "done" else ()
        delay = max(self._station_delay(station, code), self._motion_duration(station, motion))
        action = contract.action(code)
        steps = action.steps if action is not None else ()
        if station == "Develop" and code in (50, 51) and outcome == "done":
            try:
                tank_state = self._target_tank_state()
            except KeyError:
                # 精简测试/旧快照没有 Tank 辅助节点时，保留原有 action_effects 近似。
                outcome = "done"
            except (IndexError, TypeError, ValueError):
                outcome, error_code, safe_state = "error", 500, 90
            else:
                if code == 50 and tank_state in {10, 90}:
                    outcome, error_code, safe_state, retryable = "rejected", 501, 0, True
                elif code == 51 and tank_state not in {0, 98, 99}:
                    outcome, error_code, safe_state, retryable = "rejected", 511, 0, True
                else:
                    outcome = "tank_drain" if code == 50 else "tank_release"
                    try:
                        delay = max(delay, self._prepare_tank_action(code))
                    except KeyError:
                        outcome = "done"
        cycle = _Cycle(
            code, seq, now, now + delay, outcome,
            error_code=error_code, safe_state=safe_state, retryable=retryable,
            steps=steps, motion=motion,
        )
        self._cycles[station] = cycle
        self.adapter.write(self.node(station, "AcceptedSeq"), seq)
        self.adapter.write(self.node(station, "ActiveCode"), code)
        self.adapter.write(self.node(station, "Step"), 1)
        self.adapter.write(self.node(station, "State"), 10)
        return cycle, HandshakeEvent(station, "accepted", code, seq)

    def _target_tank_state(self) -> int:
        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        if not 0 <= index < len(states):
            raise IndexError("Expand_Target_Tank 超出 1..8")
        return int(states[index])

    def _prepare_tank_action(self, code: int) -> float:
        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        state = int(states[index])
        if code == 50:
            if state in {98, 99}:
                return 0.0
            states[index] = 50
            enables = list(self.adapter.read("Tank_Drain_Enable"))
            dones = list(self.adapter.read("Tank_Drain_Done"))
            cap_hits = list(self.adapter.read("Tank_Drain_CapHit"))
            enables[index], dones[index], cap_hits[index] = True, False, False
            self.adapter.write("Tank_State", states)
            self.adapter.write("Tank_Drain_Enable", enables)
            self.adapter.write("Tank_Drain_Done", dones)
            self.adapter.write("Tank_Drain_CapHit", cap_hits)
            drain = max(0.0, float(self.adapter.read("Tank_Drain_S")))
            cap = max(0.0, float(self.adapter.read("Tank_Drain_Cap_S")))
            blow = max(0.0, float(self.adapter.read("Tank_Blow_S")))
            dry = max(0.0, float(self.adapter.read("Tank_Dry_S")))
            phase_a = min(drain, cap) if cap > 0 else drain
            return phase_a + blow + dry
        return 0.0

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

    def _progress_cycle(self, station: str, cycle: _Cycle, now: float) -> None:
        duration = max(cycle.due_at - cycle.started_at, 0.0)
        fraction = 1.0 if duration == 0 else min(max(
            (now - cycle.started_at) / duration, 0.0
        ), 1.0)
        if cycle.steps:
            index = min(int(fraction * len(cycle.steps)), len(cycle.steps) - 1)
            if index != cycle.last_step_index:
                self.adapter.write(self.node(station, "Step"), cycle.steps[index])
                cycle.last_step_index = index
        for _target_name, actual_name, start, target in cycle.motion:
            self.adapter.write(actual_name, start + (target - start) * fraction)
        if cycle.outcome == "tank_drain":
            self._progress_tank_drain(cycle, now)

    def _progress_tank_drain(self, cycle: _Cycle, now: float) -> None:
        index = int(self.adapter.read("Expand_Target_Tank")) - 1
        states = list(self.adapter.read("Tank_State"))
        if int(states[index]) == 90:
            cycle.outcome, cycle.error_code, cycle.safe_state = "error", 502, 90
            cycle.due_at = now
            return
        if int(states[index]) in {98, 99}:
            cycle.due_at = min(cycle.due_at, now)
            return
        drain = max(0.0, float(self.adapter.read("Tank_Drain_S")))
        cap = max(0.0, float(self.adapter.read("Tank_Drain_Cap_S")))
        blow = max(0.0, float(self.adapter.read("Tank_Blow_S")))
        phase_a = min(drain, cap) if cap > 0 else drain
        elapsed = max(0.0, now - cycle.started_at)
        if elapsed < phase_a:
            state = 50
        elif elapsed < phase_a + blow:
            state = 55
            if cap > 0 and cap <= drain:
                cap_hits = list(self.adapter.read("Tank_Drain_CapHit"))
                cap_hits[index] = True
                self.adapter.write("Tank_Drain_CapHit", cap_hits)
        elif now < cycle.due_at:
            state = 56
        else:
            state = 98
        if int(states[index]) != state:
            states[index] = state
            self.adapter.write("Tank_State", states)
        self.adapter.write(self.node("Develop", "Step"), state)

    def _apply_process_effects(self, station: str, code: int) -> None:
        if station == "Develop" and code in {50, 51}:
            try:
                index = int(self.adapter.read("Expand_Target_Tank")) - 1
                states = list(self.adapter.read("Tank_State"))
                enables = list(self.adapter.read("Tank_Drain_Enable"))
                dones = list(self.adapter.read("Tank_Drain_Done"))
                if code == 50:
                    states[index], enables[index], dones[index] = 98, False, True
                else:
                    states[index], enables[index], dones[index] = 0, False, False
                self.adapter.write("Tank_State", states)
                self.adapter.write("Tank_Drain_Enable", enables)
                self.adapter.write("Tank_Drain_Done", dones)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        elif station == "Pump" and code in {10, 20}:
            value = code == 10
            self.process_state["vacuum_on"] = value
            try:
                self.adapter.write("Pump_Vacuum_On", value)
            except KeyError:
                pass
        elif station == "FeedLift" and code == 12:
            self.process_state["feed_count"] = max(
                0, int(self.process_state["feed_count"]) - 1
            )
        elif station == "FeedLift" and code == 21:
            self.process_state["waste_armed"] = True
        elif station == "FeedLift" and code == 22 and self.process_state["waste_armed"]:
            self.process_state["waste_count"] = int(self.process_state["waste_count"]) + 1
            self.process_state["waste_armed"] = False

    def _finish_cycle(self, station: str, cycle: _Cycle) -> HandshakeEvent:
        if cycle.outcome in {"done", "tank_drain", "tank_release"}:
            try:
                for effect in self._effects_for(station, cycle.action_code):
                    self._apply_effect(effect)
                self._apply_process_effects(station, cycle.action_code)
            except (KeyError, IndexError, TypeError, ValueError):
                state, error, safe, retryable, phase = 40, 500, 90, False, "error"
            else:
                state, error, safe, retryable, phase = 20, 0, 10, False, "completed"
        elif cycle.outcome in {"global_reject", "rejected", "reject", "unknown"}:
            state, error, safe, retryable, phase = (
                30, cycle.error_code, cycle.safe_state, cycle.retryable, "rejected"
            )
        elif cycle.outcome == "interrupt":
            state, error, safe, retryable, phase = (
                50, cycle.error_code, cycle.safe_state, cycle.retryable, "interrupted"
            )
        else:
            state, error, safe, retryable, phase = (
                40, cycle.error_code or 201, cycle.safe_state or 90,
                cycle.retryable, "error",
            )
        self.adapter.write(self.node(station, "Step"), 90)
        self.adapter.write(self.node(station, "ErrorCode"), error)
        self.adapter.write(self.node(station, "SafeState"), safe)
        self.adapter.write(self.node(station, "Retryable"), retryable)
        self.adapter.write(self.node(station, "CompletedSeq"), cycle.request_seq)
        self.adapter.write(self.node(station, "State"), state)
        self._cycles.pop(station, None)
        return HandshakeEvent(station, phase, cycle.action_code, cycle.request_seq)

    def _step_deploy(self, now: float) -> None:
        reset = bool(self.adapter.read("PLC_Deploy_Reset"))
        start = bool(self.adapter.read("PLC_Deploy_Start"))
        commit_seq = int(self.adapter.read("PLC_Deploy_CommitSeq"))
        state = int(self.adapter.read("PLC_Deploy_State"))
        if reset and not start and commit_seq == 0:
            self.adapter.write("PLC_Deploy_State", 0)
            self.adapter.write("PLC_Deploy_ErrorCode", 0)
            self._deploy_cycle = None
        elif start and not self._previous_deploy_start and state == 0:
            seq = int(self.adapter.read("PLC_Deploy_RequestSeq"))
            self.adapter.write("PLC_Deploy_AcceptedSeq", seq)
            l2_busy = any(
                int(self.adapter.read(self.node(station, "State"))) == 10
                for station in self.stations
            )
            if l2_busy:
                self.adapter.write("PLC_Deploy_State", 30)
                self.adapter.write("PLC_Deploy_ErrorCode", 1)
            elif not bool(self.adapter.read("PLC_Ready")):
                self.adapter.write("PLC_Deploy_State", 40)
                self.adapter.write("PLC_Deploy_ErrorCode", 5)
            else:
                self.adapter.write("PLC_Deploy_ErrorCode", 0)
                self.adapter.write("PLC_Deploy_State", 10)
                self._deploy_cycle = _DeployCycle(seq, now)
        elif state == 10:
            comm = list(self.adapter.read("PLC_Axis_CommOperational"))
            if len(comm) != 11 or not all(bool(value) for value in comm):
                self.adapter.write("PLC_Deploy_State", 40)
                self.adapter.write("PLC_Deploy_ErrorCode", 5)
                self._deploy_cycle = None
            else:
                delay = max(float(self.config.get("deploy_prepare_ms", 40)), 0.0) / 1000.0
                started = self._deploy_cycle.preparing_since if self._deploy_cycle else now
                if now - started >= delay - 1e-9:
                    self.adapter.write("PLC_Deploy_State", 20)
        elif state == 20 and start:
            accepted = int(self.adapter.read("PLC_Deploy_AcceptedSeq"))
            if commit_seq != 0 and commit_seq == accepted:
                self.adapter.write("PLC_Deploy_State", 25)
        elif state == 25:
            comm = list(self.adapter.read("PLC_Axis_CommOperational"))
            if len(comm) != 11 or not all(bool(value) for value in comm):
                # COMMITTED 后失败闭锁，不退回普通 ERROR。
                self.adapter.write("PLC_Deploy_ErrorCode", 5)
        self._previous_deploy_start = start

    def step(self, now: float | None = None) -> list[HandshakeEvent]:
        current = time.monotonic() if now is None else float(now)
        events: list[HandshakeEvent] = []
        try:
            self._step_deploy(current)
        except KeyError:
            # 兼容只构造单工位字段的单元测试/旧 PTLC 快照；check() 仍会报告缺项。
            pass
        for station in self.stations:
            reset = bool(self.adapter.read(self.node(station, "Reset")))
            start = bool(self.adapter.read(self.node(station, "Start")))
            state = int(self.adapter.read(self.node(station, "State")))
            if reset:
                if state != 0 or station in self._cycles:
                    old = self._cycles.get(
                        station, _Cycle(0, 0, current, current, "done")
                    )
                    self._reset_station(station)
                    events.append(HandshakeEvent(station, "reset", old.action_code, old.request_seq))
            elif start and not self._previous_start[station] and state == 0:
                cycle, accepted = self._start_cycle(station, current)
                events.append(accepted)
                if cycle.outcome != "hang" and cycle.due_at <= current:
                    events.append(self._finish_cycle(station, cycle))
            elif station in self._cycles:
                cycle = self._cycles[station]
                self._progress_cycle(station, cycle, current)
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
        for event in events:
            self._record(event)
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
    parser.add_argument("--time-scale", type=float, default=None)
    parser.add_argument("--fault-file", default=None,
                        help="运行期故障 JSON；格式 {Station:{ActionCode: outcome}}")
    parser.add_argument("--state-file", default=None,
                        help="周期写出确定性进程/动作状态 JSON")
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
            "actions": {
                station: list(simulator.contracts[station].accepts)
                for station in simulator.stations
            },
            "behavior_sha256": {
                station: simulator.contracts[station].source_sha256
                for station in simulator.stations
            },
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

        missing = simulator.check()
        if missing:
            preview = ", ".join(missing[:20])
            suffix = f" 等 {len(missing)} 项" if len(missing) > 20 else ""
            print(
                f"警告：PTLC 节点表与服务端存在漂移，相关功能将降级：{preview}{suffix}",
                file=sys.stderr,
                flush=True,
            )
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
        time_scale = float(
            args.time_scale if args.time_scale is not None else config.get("time_scale", 1.0)
        )
        if not 0 < time_scale <= 1000:
            print("time-scale 必须在 0..1000 之间", file=sys.stderr)
            return 2
        real_epoch = time.monotonic()
        sim_epoch = real_epoch
        fault_path = Path(args.fault_file).resolve() if args.fault_file else None
        state_path = Path(args.state_file).resolve() if args.state_file else None
        fault_stamp: int | None = None
        next_state_write = 0.0
        print(f"PTLC L2 代理已连接 {args.url}，工位={','.join(simulator.stations)}", flush=True)
        while not stopping:
            real_now = time.monotonic()
            sim_now = sim_epoch + (real_now - real_epoch) * time_scale
            if fault_path is not None:
                try:
                    stamp = fault_path.stat().st_mtime_ns
                    if stamp != fault_stamp:
                        payload = json.loads(fault_path.read_text(encoding="utf-8"))
                        simulator.runtime_faults.load_payload(payload or {})
                        fault_stamp = stamp
                except FileNotFoundError:
                    if fault_stamp is not None:
                        simulator.runtime_faults.clear()
                        fault_stamp = None
                except (OSError, ValueError, TypeError) as exc:
                    print(f"运行期故障文件无效: {exc}", file=sys.stderr, flush=True)
            for event in simulator.step(now=sim_now):
                print(json.dumps(event.__dict__, ensure_ascii=False), flush=True)
                if event.phase in {"completed", "rejected", "error", "interrupted"}:
                    completed += 1
            if state_path is not None and real_now >= next_state_write:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(simulator.snapshot(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                next_state_write = real_now + 0.25
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
