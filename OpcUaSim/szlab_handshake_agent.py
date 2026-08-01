"""
SZLab Poly Studio OPC UA 握手仿真驱动。

本进程作为 OPC UA Client 连接已有仿真 Server，并按 Uni-Lab-SZLab 的
``szlab_workflow_handshake.py`` 模拟 PLC 侧行为。与简单的通用握手不同，
这里保留了每个工站自己的接单和复位语义：

* Robot：支持任务 7/8/11/12/13/15/16，并同步物料在位传感器；
* S04：支持 1-6 号磁搅位，按上位机写入的磁搅时间反馈完成；
* S06：参数标志复位即可重新允许加工，工艺号可以保留；
* S07/S08：返回工艺号，S08 还会等待瓶盖暂存位复位；
* S09：用持续保留的工艺号接单，避免漏掉约 0.1 秒的参数完成脉冲，
  同时屏蔽代理启动前已经存在的无脉冲残留工艺号。

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
from typing import Any, Dict, Iterable, Literal, Optional

from opcua import Client, ua

try:
    from .common import load_yaml, setup_logging
except ImportError:  # Direct `python szlab_handshake_agent.py` compatibility.
    from common import load_yaml, setup_logging


log = setup_logging("SZLab-Handshake")

ROBOT_HOME = "Robot_Home"
ROBOT_WRITE_ALLOWED = "Robot_任务允许写入"
ROBOT_WRITE_DONE = "Robot_任务写入完成"
ROBOT_TASK_NUMBER = "任务号"
ROBOT_TASK_COMPLETE = "Robot_任务完成"
S04_ROBOT_POSITION = "S04取放料编号"

S05_DONE = "S05加工完成"
S05_RESULT = "S05拍照结果"

S06_READY = "S06准备信号"
S06_ALLOW = "S06允许加工"
S06_PROCESS = "S06工艺选择"
S06_PARAMS_WRITTEN = "S06参数写入完成"
S06_DONE = "S06加工完成"
S06_BEAKER_SENSOR = "传感器状态_上位机[3].NO[1]"
S06_STORAGE_BOTTLE_SENSOR = {
    1: "传感器状态_上位机[4].NO[12]",
    2: "传感器状态_上位机[5].NO[1]",
}

S071_ROBOT_POSITION = "S071取放料编号"
S071_SENSOR_BY_SLOT = {
    slot: f"传感器状态_上位机[3].NO[{slot + 7}]"
    for slot in range(1, 7)
}
S072_SENSOR_BY_POSITION = {
    1: "传感器状态_上位机[3].NO[14]",
    2: "传感器状态_上位机[3].NO[15]",
}

S07_HOME = "S07原点信号"
S07_ALLOW = "S07允许加工"
S07_PROCESS = "S07工艺选择"
S07_PARAMS_WRITTEN = "S07参数写入完成"
S07_DONE = "S07工艺完成"
S07_PROCESS_LABELS = {
    1: "粉罐扫码盘点",
    2: "替换粉罐旋转到进料位",
    3: "注粉",
}

S08_HOME = "S08原点信号"
S08_ALLOW = "S08允许加工"
S08_PROCESS = "S08工艺选择"
S08_PARAMS_WRITTEN = "S08参数写入完成"
S08_DONE = "S08工艺完成"
S08_CAP_STORAGE_SLOT = "S082瓶盖暂存位"
S08_STATION_STATUS = "工站状态[7]"
S08_PROCESS_LABELS = {
    1: "500 mL 样品瓶开盖",
    2: "500 mL 样品瓶关盖",
    3: "250 mL 样品瓶开盖",
    4: "250 mL 样品瓶关盖",
    5: "100 mL 液体瓶开盖",
    6: "100 mL 液体瓶关盖",
}

S09_PROCESS = "S09工艺选择"
S09_PARAMS_WRITTEN = "S09参数写入完成"
S09_DONE = "S09工艺完成"
S09_ALLOW = "S09允许加工"
S09_STATION_STATUS = "工站状态[8]"
S09_PROCESS_LABELS = {
    1: "去安全位1",
    2: "去安全位2",
    3: "去安全位3",
    4: "去安全位4",
    5: "取 TIP",
    6: "放 TIP",
    7: "液体瓶取液",
    8: "烧杯放液",
    9: "测密度抽液",
    10: "测密度排液",
}

SUPPORTED_ACTIONS = (
    "szlab_mixer_robot.submit_place_to_s04",
    "szlab_mixer_stirrer.run_stirring",
    "szlab_mixer_robot.submit_pick_from_s04",
    "szlab_mixer_photoshotting.take_photo",
    "szlab_mixer_pump.run_solvent_addition",
    "szlab_mixer_robot.submit_place_to_s06",
    "szlab_mixer_robot.submit_pick_from_s06",
    "szlab_mixer_pipetting_station.prepare_liquid_station",
    "szlab_mixer_pipetting_station.bind_sample_to_station",
    "szlab_mixer_pipetting_station.add_liquid",
    "szlab_mixer_pipetting_station.release_station",
    "szlab_mixer_robot.submit_place_to_s071",
    "szlab_mixer_robot.submit_place_to_s072",
    "szlab_mixer_robot.submit_pick_from_s072",
    "szlab_s07_solid_addition.scan_powder_cartridges",
    "szlab_s07_solid_addition.rotate_powder_cartridge_to_feed",
    "szlab_s07_solid_addition.dose_powder",
    "szlab_s08_cap_station.process_cap_with_sample_parts",
    "szlab_poly_plc.get_stack_status",
)

WORKFLOW_IDS = (
    "szlab_magnetic_stirring_workflow",
    "szlab_photoshotting_workflow",
    "szlab_robot_action_workflow",
    "s04_robot_stirring_workflow",
    "s06_robot_workflow",
    "s07_robot_workflow",
    "szlab_s07_solid_addition_workflow",
    "s08_cap_workflow",
    "szlab_s09_pipetting_workflow",
    "szlab_stack_s05_s06_workflow",
    "szlab_mixer_workflow",
    "szlab_mixer_pump_production",
    "szlab_robot_liquid_stirring_demo_workflow",
)

# 组件名同时也是节点发现和轮询的能力组。photo 只负责初始化 S05 的只读
# 完成信号，没有周期状态机。
WORKFLOW_COMPONENTS = {
    "szlab_magnetic_stirring_workflow": frozenset({"s04"}),
    "szlab_photoshotting_workflow": frozenset({"photo"}),
    "szlab_robot_action_workflow": frozenset({"robot"}),
    "s04_robot_stirring_workflow": frozenset({"robot", "s04"}),
    "s06_robot_workflow": frozenset({"robot", "s06"}),
    "s07_robot_workflow": frozenset({"robot"}),
    "szlab_s07_solid_addition_workflow": frozenset({"s07"}),
    "s08_cap_workflow": frozenset({"s08"}),
    "szlab_s09_pipetting_workflow": frozenset({"s09"}),
    "szlab_stack_s05_s06_workflow": frozenset({"photo", "s06"}),
    "szlab_mixer_workflow": frozenset({"s06"}),
    "szlab_mixer_pump_production": frozenset({"s06"}),
    "szlab_robot_liquid_stirring_demo_workflow": frozenset(
        {"robot", "s06", "s04"}
    ),
}
ALL_COMPONENTS = frozenset().union(*WORKFLOW_COMPONENTS.values())

WORKFLOW_ROBOT_TASKS = {
    "szlab_robot_action_workflow": frozenset({7, 8}),
    "s04_robot_stirring_workflow": frozenset({7, 8}),
    "s06_robot_workflow": frozenset({11, 12}),
    "s07_robot_workflow": frozenset({13, 15, 16}),
    "szlab_robot_liquid_stirring_demo_workflow": frozenset({7, 11, 12}),
}
ALL_ROBOT_TASKS = frozenset({7, 8, 11, 12, 13, 15, 16})

ROBOT_ACTION_BY_TASK = {
    7: SUPPORTED_ACTIONS[0],
    8: SUPPORTED_ACTIONS[2],
    11: SUPPORTED_ACTIONS[5],
    12: SUPPORTED_ACTIONS[6],
    13: SUPPORTED_ACTIONS[11],
    15: SUPPORTED_ACTIONS[12],
    16: SUPPORTED_ACTIONS[13],
}
S07_ACTION_BY_PROCESS = {
    1: SUPPORTED_ACTIONS[14],
    2: SUPPORTED_ACTIONS[15],
    3: SUPPORTED_ACTIONS[16],
}


def s04_sensor(position: int) -> str:
    position = int(position)
    if position not in range(1, 7):
        raise ValueError("S04 position 必须在 1-6 范围内")
    return f"传感器状态_上位机[2].NO[{position + 9}]"


def s04_allow(position: int) -> str:
    return f"S04{int(position)}允许加工"


def s04_process(position: int) -> str:
    return f"S04{int(position)}磁搅工艺选择"


def s04_params_written(position: int) -> str:
    return f"S04{int(position)}参数写入完成"


def s04_done(position: int) -> str:
    return f"S04{int(position)}加工完成"


def s04_duration(position: int) -> str:
    return f"磁搅时间设置_上位机[{int(position) - 1}]"


def s09_remaining_volume(bottle: int) -> str:
    return f"S09液体瓶{int(bottle)}剩余液量"


def s08_cap_cache(slot: int, index: int) -> str:
    return f"S082_{int(slot)}数据缓存[{int(index)}]"


def workflow_components(workflow: str) -> frozenset[str]:
    workflow = str(workflow or "all")
    if workflow == "all":
        return ALL_COMPONENTS
    try:
        return WORKFLOW_COMPONENTS[workflow]
    except KeyError as exc:
        raise ValueError(f"不支持的握手工作流: {workflow}") from exc


def workflow_robot_tasks(workflow: str) -> frozenset[int]:
    workflow = str(workflow or "all")
    if workflow == "all":
        return ALL_ROBOT_TASKS
    if workflow not in WORKFLOW_COMPONENTS:
        raise ValueError(f"不支持的握手工作流: {workflow}")
    return WORKFLOW_ROBOT_TASKS.get(workflow, frozenset())


def workflow_s04_positions(workflow: str, position: int) -> frozenset[int]:
    position = int(position)
    if position not in range(1, 7):
        raise ValueError("position 必须在 1-6 范围内")
    if workflow == "all":
        return frozenset(range(1, 7))
    components = workflow_components(workflow)
    robot_tasks = workflow_robot_tasks(workflow)
    if "s04" in components or robot_tasks.intersection({7, 8}):
        return frozenset({position})
    return frozenset()


@dataclass
class CycleState:
    phase: Literal["idle", "executing", "await_reset"] = "idle"
    due_at: float = 0.0
    process: int = 0
    position: int = 0
    sensor: str = ""
    duration_seconds: float = 0.0
    waiting_for_params_clear: bool = False


@dataclass(frozen=True)
class HandshakeEvent:
    action: str
    phase: Literal["accepted", "completed", "reset"]
    details: Dict[str, Any]


def default_initial_values(
    *,
    workflow: str = "all",
    position: int = 1,
    pump: int = 1,
    s06_robot_workflow: bool = False,
    s09_pipetting_workflow: bool = True,
    s09_remaining_volume_ml: float = 100.0,
) -> Dict[str, Any]:
    """返回仿真器拥有的 PLC 输出初值，不覆盖 PC 侧输入参数。"""
    components = set(workflow_components(workflow))
    robot_tasks = workflow_robot_tasks(workflow)
    s04_positions = workflow_s04_positions(workflow, position)
    if int(pump) not in (1, 2, 3):
        raise ValueError("pump 必须是 1、2 或 3")
    if workflow == "all" and not s09_pipetting_workflow:
        components.discard("s09")
    if "s09" in components and float(s09_remaining_volume_ml) <= 0:
        raise ValueError("S09 初始液体余量必须大于 0 mL")

    values: Dict[str, Any] = {}
    if "robot" in components:
        values.update(
            {
                ROBOT_HOME: True,
                ROBOT_WRITE_ALLOWED: True,
                ROBOT_WRITE_DONE: False,
                ROBOT_TASK_COMPLETE: 0,
            }
        )
    if robot_tasks.intersection({7, 8}):
        for s04_position in s04_positions:
            values[s04_sensor(s04_position)] = False
    if "s04" in components:
        for s04_position in s04_positions:
            values.update(
                {
                    s04_allow(s04_position): True,
                    s04_done(s04_position): False,
                }
            )
    if "photo" in components:
        values.update({S05_DONE: True, S05_RESULT: 1})
    if "s06" in components:
        values.update(
            {
                S06_READY: True,
                S06_ALLOW: True,
                S06_DONE: False,
                S06_BEAKER_SENSOR: not bool(s06_robot_workflow),
            }
        )
        for bottle in ((1, 2) if int(pump) == 3 else (int(pump),)):
            values[S06_STORAGE_BOTTLE_SENSOR[bottle]] = True
    if robot_tasks.intersection({11, 12}):
        values[S06_BEAKER_SENSOR] = not bool(s06_robot_workflow)
    if robot_tasks.intersection({13, 15, 16}):
        for sensor in S071_SENSOR_BY_SLOT.values():
            values[sensor] = False
        values[S072_SENSOR_BY_POSITION[1]] = False
    if "s07" in components:
        values.update({S07_HOME: True, S07_ALLOW: True, S07_DONE: 0})
    if "s08" in components:
        values.update(
            {
                S08_HOME: True,
                S08_ALLOW: True,
                S08_DONE: 0,
                S08_STATION_STATUS: 2,
                **{s08_cap_cache(1, index): 0 for index in range(30)},
            }
        )
    if "s09" in components:
        values.update(
            {
                S09_STATION_STATUS: 2,
                S09_ALLOW: True,
                S09_DONE: 0,
                **{
                    s09_remaining_volume(bottle): float(s09_remaining_volume_ml)
                    for bottle in range(1, 6)
                },
            }
        )
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
        workflow: str = "all",
        position: int = 1,
        pump: int = 1,
        s06_robot_workflow: bool = False,
        s09_pipetting_workflow: bool = True,
        s09_remaining_volume_ml: float = 100.0,
    ) -> None:
        self.url = url
        self.namespace_index = int(namespace_index)
        self.node_prefix = node_prefix
        self.delay_ms = max(0, int(delay_ms))
        self.poll_ms = max(5, int(poll_ms))
        self.delays = {str(key): int(value) for key, value in (delays or {}).items()}
        self.strict = bool(strict)
        self.workflow = str(workflow or "all")
        self.position = int(position)
        self.pump = int(pump)
        self.s06_robot_workflow = bool(
            s06_robot_workflow
            or self.workflow
            in (
                "s06_robot_workflow",
                "szlab_robot_liquid_stirring_demo_workflow",
            )
        )
        self.s09_pipetting_workflow = bool(
            s09_pipetting_workflow
            or self.workflow == "szlab_s09_pipetting_workflow"
        )
        components = set(workflow_components(self.workflow))
        if self.workflow == "all" and not self.s09_pipetting_workflow:
            components.discard("s09")
        self.enabled_components = frozenset(components)
        self.enabled_robot_tasks = workflow_robot_tasks(self.workflow)
        self.s04_positions = workflow_s04_positions(self.workflow, self.position)
        self.s09_remaining_volume_ml = float(s09_remaining_volume_ml)
        defaults = default_initial_values(
            workflow=self.workflow,
            position=self.position,
            pump=self.pump,
            s06_robot_workflow=self.s06_robot_workflow,
            s09_pipetting_workflow=self.s09_pipetting_workflow,
            s09_remaining_volume_ml=self.s09_remaining_volume_ml,
        )
        self.initial_values = {**defaults, **dict(initial_values or {})}
        self.client = client or Client(url, timeout=4)
        self.nodes: Dict[str, Any] = {}
        self.enabled_groups: set[str] = set()
        self.enabled_s04_positions: set[int] = set()
        self.robot = CycleState()
        self.s04_cycles = {position: CycleState(position=position) for position in range(1, 7)}
        self.s06 = CycleState()
        self.s07 = CycleState()
        self.s08 = CycleState()
        self.s09 = CycleState()
        self._s071_loaded_sensor = ""
        self._s09_startup_stale_process: Optional[int] = None
        self._s09_startup_guard_captured = False
        self._warned_inputs: set[tuple[str, int]] = set()
        self.completed_actions = 0
        self._stop = threading.Event()
        self._connected = False

    @property
    def required_names(self) -> set[str]:
        names = set(self.initial_values)
        components = set(self.enabled_components)
        if self.workflow == "all" and not self.s09_pipetting_workflow:
            components.discard("s09")
        if "robot" in components:
            names.update(
                {
                    ROBOT_HOME,
                    ROBOT_WRITE_ALLOWED,
                    ROBOT_WRITE_DONE,
                    ROBOT_TASK_NUMBER,
                    ROBOT_TASK_COMPLETE,
                }
            )
            if self.enabled_robot_tasks.intersection({7, 8}):
                names.add(S04_ROBOT_POSITION)
                names.update(s04_sensor(item) for item in self.s04_positions)
            if self.enabled_robot_tasks.intersection({11, 12}):
                names.add(S06_BEAKER_SENSOR)
            if 13 in self.enabled_robot_tasks:
                names.add(S071_ROBOT_POSITION)
                names.update(S071_SENSOR_BY_SLOT.values())
            if self.enabled_robot_tasks.intersection({15, 16}):
                names.add(S072_SENSOR_BY_POSITION[1])
        if "s04" in components:
            for s04_position in self.s04_positions:
                names.update(
                    {
                        s04_allow(s04_position),
                        s04_process(s04_position),
                        s04_params_written(s04_position),
                        s04_done(s04_position),
                        s04_duration(s04_position),
                    }
                )
        if "s06" in components:
            names.update(
                {
                    S06_READY,
                    S06_ALLOW,
                    S06_PROCESS,
                    S06_PARAMS_WRITTEN,
                    S06_DONE,
                    S06_BEAKER_SENSOR,
                }
            )
        if "s07" in components:
            names.update(
                {
                    S07_HOME,
                    S07_ALLOW,
                    S07_PROCESS,
                    S07_PARAMS_WRITTEN,
                    S07_DONE,
                }
            )
        if "s08" in components:
            names.update(
                {
                    S08_HOME,
                    S08_ALLOW,
                    S08_PROCESS,
                    S08_PARAMS_WRITTEN,
                    S08_DONE,
                    S08_CAP_STORAGE_SLOT,
                    S08_STATION_STATUS,
                }
            )
        if "s09" in components:
            names.update(
                {
                    S09_PROCESS,
                    S09_PARAMS_WRITTEN,
                    S09_DONE,
                    S09_ALLOW,
                    S09_STATION_STATUS,
                }
            )
        if "photo" in components:
            names.update(
                {
                    S05_DONE,
                    S05_RESULT,
                }
            )
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
        self._prepare_capabilities()
        log.info(
            "已连接 %s，工作流=%s，解析节点 %d 个，启用工站：%s",
            self.url,
            self.workflow,
            len(self.nodes),
            ", ".join(sorted(self.enabled_groups)) or "无",
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

    def _prepare_capabilities(self) -> None:
        self.enabled_groups.clear()
        self.enabled_s04_positions.clear()
        components = set(self.enabled_components)
        if self.workflow == "all" and not self.s09_pipetting_workflow:
            components.discard("s09")
        groups: Dict[str, Iterable[str]] = {
            "robot": (
                ROBOT_HOME,
                ROBOT_WRITE_ALLOWED,
                ROBOT_WRITE_DONE,
                ROBOT_TASK_NUMBER,
                ROBOT_TASK_COMPLETE,
            ),
            "s06": (S06_ALLOW, S06_PROCESS, S06_PARAMS_WRITTEN, S06_DONE),
            "s07": (S07_ALLOW, S07_PROCESS, S07_PARAMS_WRITTEN, S07_DONE),
            "s08": (
                S08_ALLOW,
                S08_PROCESS,
                S08_PARAMS_WRITTEN,
                S08_DONE,
                S08_CAP_STORAGE_SLOT,
            ),
            "s09": (S09_ALLOW, S09_PROCESS, S09_PARAMS_WRITTEN, S09_DONE),
            "photo": (S05_DONE, S05_RESULT),
        }
        missing_messages: list[str] = []
        for group, needed in groups.items():
            if group not in components:
                continue
            missing = [name for name in needed if name not in self.nodes]
            if missing:
                missing_messages.append(f"{group}: {', '.join(missing)}")
            else:
                self.enabled_groups.add(group)

        for position in sorted(self.s04_positions if "s04" in components else ()):
            needed = (
                s04_allow(position),
                s04_process(position),
                s04_params_written(position),
                s04_done(position),
            )
            missing = [name for name in needed if name not in self.nodes]
            if missing:
                missing_messages.append(f"s04:{position}: {', '.join(missing)}")
            else:
                self.enabled_s04_positions.add(position)
        if self.enabled_s04_positions:
            self.enabled_groups.add("s04")

        if self.strict:
            missing_all = sorted(self.required_names.difference(self.nodes))
            if missing_all:
                raise RuntimeError("SZLab 握手节点不完整：" + ", ".join(missing_all))
        elif missing_messages:
            log.warning("部分握手因 CSV/Server 缺少节点而跳过：%s", "; ".join(missing_messages))

    def initialize(self) -> None:
        # 先快照 PC 输入，再写仿真器输出。否则远程初始化节点较多时，
        # 初始化期间到达的新命令可能被误判为启动残留值。
        self._reset_internal_state()
        self._capture_s09_startup_guard()
        written = 0
        for name, value in self.initial_values.items():
            if name in self.nodes:
                self._write(name, value)
                written += 1
        log.info("PLC 侧握手初始状态已写入：%d 个节点", written)

    def cleanup(self) -> None:
        """安全释放本仿真器拥有的输出，不改写 PC 侧任务号和工艺参数。"""
        cleaned = 0
        for name, value in self._cleanup_values().items():
            if name in self.nodes:
                self._write(name, value)
                cleaned += 1
        self._reset_internal_state()
        log.info("PLC 侧握手输出已清理：%d 个节点", cleaned)

    def _cleanup_values(self) -> Dict[str, Any]:
        owned_values = default_initial_values(
            workflow=self.workflow,
            position=self.position,
            pump=self.pump,
            s06_robot_workflow=self.s06_robot_workflow,
            s09_pipetting_workflow=self.s09_pipetting_workflow,
            s09_remaining_volume_ml=self.s09_remaining_volume_ml,
        )
        # 启动时将 write_done 置为基线 False，但退出时它属于 PC 输入，不能覆盖。
        owned_values.pop(ROBOT_WRITE_DONE, None)
        return {
            name: (False if isinstance(value, bool) else 0)
            for name, value in owned_values.items()
        }

    def _reset_internal_state(self) -> None:
        self.robot = CycleState()
        self.s04_cycles = {position: CycleState(position=position) for position in range(1, 7)}
        self.s06 = CycleState()
        self.s07 = CycleState()
        self.s08 = CycleState()
        self.s09 = CycleState()
        self._s071_loaded_sensor = ""
        self._s09_startup_stale_process = None
        self._s09_startup_guard_captured = False
        self._warned_inputs.clear()

    def _capture_s09_startup_guard(self) -> None:
        """快照 S09 启动前的 PC 输入，避免把上次中断的工艺当成新命令。"""
        self._s09_startup_guard_captured = True
        self._s09_startup_stale_process = None
        if "s09" not in self.enabled_groups:
            return
        if S09_PROCESS not in self.nodes or S09_PARAMS_WRITTEN not in self.nodes:
            return
        process = int(self._read(S09_PROCESS) or 0)
        params_written = bool(self._read(S09_PARAMS_WRITTEN))
        if process in S09_PROCESS_LABELS and not params_written:
            self._s09_startup_stale_process = process
            log.warning(
                "S09 启动时检测到无参数脉冲的残留工艺 %d (%s)；"
                "等待工艺号变化或新的参数完成脉冲",
                process,
                S09_PROCESS_LABELS[process],
            )

    def run_forever(self, initialize: bool = True) -> None:
        if initialize:
            self.initialize()
        elif not self._s09_startup_guard_captured:
            self._capture_s09_startup_guard()
        log.info("SZLab 握手仿真已启动，poll=%dms", self.poll_ms)
        while not self._stop.wait(self.poll_ms / 1000.0):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("握手轮询异常：%s", exc)

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: Optional[float] = None) -> list[HandshakeEvent]:
        now = time.monotonic() if now is None else float(now)
        events: list[HandshakeEvent] = []
        if "robot" in self.enabled_groups:
            events.extend(self._tick_robot(now))
        for position in sorted(self.enabled_s04_positions):
            events.extend(self._tick_s04(position, now))
        if "s06" in self.enabled_groups:
            events.extend(self._tick_boolean_station("s06", self.s06, now))
        if "s07" in self.enabled_groups:
            events.extend(self._tick_s07(now))
        if "s08" in self.enabled_groups:
            events.extend(self._tick_s08(now))
        if "s09" in self.enabled_groups:
            events.extend(self._tick_s09(now))
        self.completed_actions += sum(event.phase == "completed" for event in events)
        for event in events:
            log.info("握手 %s %s：%s", event.action, event.phase, event.details)
        return events

    def _tick_robot(self, now: float) -> list[HandshakeEvent]:
        cycle = self.robot
        events: list[HandshakeEvent] = []
        if cycle.phase == "idle":
            write_done = bool(self._read(ROBOT_WRITE_DONE))
            task = int(self._read(ROBOT_TASK_NUMBER) or 0)
            if write_done and task in ROBOT_ACTION_BY_TASK:
                target = self._robot_target(task)
                if target is None:
                    self._warn_unsupported_input("robot", task)
                    return events
                position, sensor = target
                self._write(ROBOT_WRITE_ALLOWED, False)
                self._write(ROBOT_HOME, False)
                self._write(ROBOT_TASK_COMPLETE, 0)
                cycle.phase = "executing"
                cycle.process = task
                cycle.position = position
                cycle.sensor = sensor
                cycle.due_at = now + self._delay_seconds("robot")
                events.append(
                    HandshakeEvent(
                        ROBOT_ACTION_BY_TASK[task],
                        "accepted",
                        {
                            "task_number": task,
                            **({"position": position} if position else {}),
                            **({"sensor": sensor} if sensor else {}),
                        },
                    )
                )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            occupied = cycle.process in (7, 11, 13, 15)
            if cycle.sensor:
                self._write(cycle.sensor, occupied)
            rearmed_sensor = ""
            if cycle.process == 13:
                self._s071_loaded_sensor = cycle.sensor
            elif cycle.process == 16 and self._s071_loaded_sensor:
                rearmed_sensor = self._s071_loaded_sensor
                self._write(rearmed_sensor, False)
                self._s071_loaded_sensor = ""
            self._write(ROBOT_HOME, True)
            self._write(ROBOT_TASK_COMPLETE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    ROBOT_ACTION_BY_TASK[cycle.process],
                    "completed",
                    {
                        "task_number": cycle.process,
                        "occupied": occupied,
                        **({"position": cycle.position} if cycle.position else {}),
                        **({"sensor": cycle.sensor} if cycle.sensor else {}),
                        **({"rearmed_sensor": rearmed_sensor} if rearmed_sensor else {}),
                    },
                )
            )
        elif cycle.phase == "await_reset":
            write_done = bool(self._read(ROBOT_WRITE_DONE))
            observed_task = int(self._read(ROBOT_TASK_NUMBER) or 0)
            if not write_done:
                self._write(ROBOT_TASK_COMPLETE, 0)
                self._write(ROBOT_WRITE_ALLOWED, True)
                self._write(ROBOT_HOME, True)
                events.append(
                    HandshakeEvent(
                        ROBOT_ACTION_BY_TASK[cycle.process],
                        "reset",
                        {
                            "task_number": cycle.process,
                            "observed_task_number": observed_task,
                        },
                    )
                )
                self.robot = CycleState()
        return events

    def _robot_target(self, task: int) -> Optional[tuple[int, str]]:
        if task not in self.enabled_robot_tasks:
            return None
        position = 0
        sensor = ""
        if task in (7, 8):
            if S04_ROBOT_POSITION not in self.nodes:
                return None
            position = int(self._read(S04_ROBOT_POSITION) or 0)
            if position not in self.s04_positions:
                return None
            sensor = s04_sensor(position)
        elif task in (11, 12):
            sensor = S06_BEAKER_SENSOR
        elif task == 13:
            if S071_ROBOT_POSITION not in self.nodes:
                return None
            position = int(self._read(S071_ROBOT_POSITION) or 0)
            if position not in S071_SENSOR_BY_SLOT:
                return None
            sensor = S071_SENSOR_BY_SLOT[position]
        elif task in (15, 16):
            position = 1
            sensor = S072_SENSOR_BY_POSITION[position]
        if sensor and sensor not in self.nodes:
            return None
        return position, sensor

    def _tick_s04(self, position: int, now: float) -> list[HandshakeEvent]:
        cycle = self.s04_cycles[position]
        events: list[HandshakeEvent] = []
        params_written = bool(self._read(s04_params_written(position)))
        process = int(self._read(s04_process(position)) or 0)
        if cycle.phase == "idle" and params_written and process in (1, 2, 3):
            self._write(s04_allow(position), False)
            self._write(s04_done(position), False)
            cycle.phase = "executing"
            cycle.process = process
            cycle.duration_seconds = self._s04_duration_seconds(position)
            cycle.due_at = now + cycle.duration_seconds
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[1],
                    "accepted",
                    {
                        "process": process,
                        "position": position,
                        "duration_seconds": cycle.duration_seconds,
                    },
                )
            )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self._write(s04_done(position), True)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[1],
                    "completed",
                    {
                        "process": cycle.process,
                        "position": position,
                        "duration_seconds": cycle.duration_seconds,
                    },
                )
            )
        elif cycle.phase == "await_reset" and not params_written and process == 0:
            self._write(s04_done(position), False)
            self._write(s04_allow(position), True)
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[1],
                    "reset",
                    {"process": cycle.process, "position": position},
                )
            )
            self.s04_cycles[position] = CycleState(position=position)
        return events

    def _tick_boolean_station(
        self,
        group: str,
        cycle: CycleState,
        now: float,
    ) -> list[HandshakeEvent]:
        if group != "s06":
            raise ValueError(f"未知布尔握手工站：{group}")
        events: list[HandshakeEvent] = []
        params_written = bool(self._read(S06_PARAMS_WRITTEN))
        process = int(self._read(S06_PROCESS) or 0)
        if cycle.phase == "idle" and params_written and process in (1, 2, 3):
            self._write(S06_ALLOW, False)
            self._write(S06_DONE, False)
            cycle.phase = "executing"
            cycle.process = process
            cycle.due_at = now + self._delay_seconds("s06")
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[4],
                    "accepted",
                    {"process": process},
                )
            )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self._write(S06_DONE, True)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[4],
                    "completed",
                    {"process": cycle.process},
                )
            )
        elif cycle.phase == "await_reset" and not params_written:
            self._write(S06_DONE, False)
            self._write(S06_ALLOW, True)
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[4],
                    "reset",
                    {"process": cycle.process, "observed_process": process},
                )
            )
            self.s06 = CycleState()
        return events

    def _tick_s07(self, now: float) -> list[HandshakeEvent]:
        cycle = self.s07
        events: list[HandshakeEvent] = []
        process = int(self._read(S07_PROCESS) or 0)
        params_written = bool(self._read(S07_PARAMS_WRITTEN))
        if cycle.phase == "idle" and params_written and process in S07_PROCESS_LABELS:
            self._write(S07_ALLOW, False)
            self._write(S07_DONE, 0)
            cycle.phase = "executing"
            cycle.process = process
            cycle.due_at = now + self._delay_seconds("s07")
            events.append(
                HandshakeEvent(
                    S07_ACTION_BY_PROCESS[process],
                    "accepted",
                    {"process": process, "process_label": S07_PROCESS_LABELS[process]},
                )
            )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self._write(S07_DONE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    S07_ACTION_BY_PROCESS[cycle.process],
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S07_PROCESS_LABELS[cycle.process],
                    },
                )
            )
        elif cycle.phase == "await_reset" and not params_written and process == 0:
            self._write(S07_DONE, 0)
            self._write(S07_ALLOW, True)
            events.append(
                HandshakeEvent(
                    S07_ACTION_BY_PROCESS[cycle.process],
                    "reset",
                    {
                        "process": cycle.process,
                        "process_label": S07_PROCESS_LABELS[cycle.process],
                    },
                )
            )
            self.s07 = CycleState()
        return events

    def _tick_s08(self, now: float) -> list[HandshakeEvent]:
        cycle = self.s08
        events: list[HandshakeEvent] = []
        process = int(self._read(S08_PROCESS) or 0)
        params_written = bool(self._read(S08_PARAMS_WRITTEN))
        cap_storage_slot = int(self._read(S08_CAP_STORAGE_SLOT) or 0)
        if cycle.phase == "idle" and params_written and process in S08_PROCESS_LABELS:
            self._write(S08_ALLOW, False)
            self._write(S08_DONE, 0)
            cycle.phase = "executing"
            cycle.process = process
            cycle.position = cap_storage_slot
            cycle.due_at = now + self._delay_seconds("s08")
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[17],
                    "accepted",
                    {
                        "process": process,
                        "process_label": S08_PROCESS_LABELS[process],
                        "cap_storage_slot": cap_storage_slot,
                    },
                )
            )
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self._write(S08_DONE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[17],
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S08_PROCESS_LABELS[cycle.process],
                        "cap_storage_slot": cycle.position,
                    },
                )
            )
        elif (
            cycle.phase == "await_reset"
            and not params_written
            and process == 0
            and cap_storage_slot == 0
        ):
            self._write(S08_DONE, 0)
            self._write(S08_ALLOW, True)
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[17],
                    "reset",
                    {
                        "process": cycle.process,
                        "process_label": S08_PROCESS_LABELS[cycle.process],
                        "cap_storage_slot": cycle.position,
                    },
                )
            )
            self.s08 = CycleState()
        return events

    def _tick_s09(self, now: float) -> list[HandshakeEvent]:
        cycle = self.s09
        events: list[HandshakeEvent] = []
        process = int(self._read(S09_PROCESS) or 0)
        params_written = bool(self._read(S09_PARAMS_WRITTEN))
        if not self._s09_startup_guard_captured:
            self._capture_s09_startup_guard()
        stale_process = self._s09_startup_stale_process
        if stale_process is not None:
            if not params_written and process == stale_process:
                return events
            self._s09_startup_stale_process = None
            log.info(
                "S09 启动残留保护已解除：启动工艺=%d，当前工艺=%d，参数完成=%s",
                stale_process,
                process,
                params_written,
            )
        if cycle.phase == "idle" and process in S09_PROCESS_LABELS:
            self._write(S09_ALLOW, False)
            self._write(S09_DONE, 0)
            cycle.phase = "executing"
            cycle.process = process
            cycle.duration_seconds = self._delay_seconds("s09")
            cycle.waiting_for_params_clear = params_written
            cycle.due_at = (
                0.0
                if cycle.waiting_for_params_clear
                else now + cycle.duration_seconds
            )
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[9],
                    "accepted",
                    {
                        "process": process,
                        "process_label": S09_PROCESS_LABELS[process],
                        "params_written": params_written,
                        "duration_seconds": cycle.duration_seconds,
                    },
                )
            )
        elif cycle.phase == "executing" and cycle.waiting_for_params_clear:
            if not params_written:
                # Edge 的参数脉冲默认持续约 0.1 秒。从脉冲回落后再计时，
                # 确保 Edge 已经进入完成信号等待，不会把新完成号误认为旧周期。
                cycle.waiting_for_params_clear = False
                cycle.due_at = now + cycle.duration_seconds
        elif cycle.phase == "executing" and now >= cycle.due_at:
            self._write(S09_DONE, cycle.process)
            cycle.phase = "await_reset"
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[9],
                    "completed",
                    {
                        "process": cycle.process,
                        "process_label": S09_PROCESS_LABELS[cycle.process],
                        "duration_seconds": cycle.duration_seconds,
                    },
                )
            )
        elif cycle.phase == "await_reset" and process != cycle.process:
            self._write(S09_DONE, 0)
            self._write(S09_ALLOW, True)
            events.append(
                HandshakeEvent(
                    SUPPORTED_ACTIONS[9],
                    "reset",
                    {"process": cycle.process, "observed_process": process},
                )
            )
            self.s09 = CycleState()
        return events

    def _delay_seconds(self, group: str, key: Optional[str] = None) -> float:
        delay = self.delays.get(key or "", self.delays.get(group, self.delay_ms))
        return max(0, int(delay)) / 1000.0

    def _s04_duration_seconds(self, position: int) -> float:
        name = s04_duration(position)
        if name not in self.nodes:
            return self._delay_seconds("s04", f"s04:{position}")
        try:
            duration_ms = int(self._read(name) or 0)
        except (TypeError, ValueError):
            log.warning(
                "%s 无法解析，改用配置延时",
                name,
            )
            return self._delay_seconds("s04", f"s04:{position}")
        return max(0, duration_ms) / 1000.0

    def _warn_unsupported_input(self, group: str, value: int) -> None:
        warning = (group, value)
        if warning in self._warned_inputs:
            return
        self._warned_inputs.add(warning)
        log.warning(
            "%s 收到无法执行的值 %s：当前工作流未启用、位置无效或对应传感器节点缺失",
            group,
            value,
        )

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


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--workflow",
        choices=("all", *WORKFLOW_IDS),
        default=None,
        help="只启用指定工作流所需的节点、初值和握手状态机",
    )
    parser.add_argument(
        "--position",
        type=int,
        choices=range(1, 7),
        default=None,
        help="S04 调试位置，1-6",
    )
    parser.add_argument("--pump", type=int, choices=(1, 2, 3), default=None)
    parser.add_argument(
        "--s06-robot-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="让 S06 烧杯在位信号由机器人任务 11/12 驱动",
    )
    parser.add_argument(
        "--s09-pipetting-workflow",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="初始化并响应完整 S09 移液工作流",
    )
    parser.add_argument("--s09-remaining-volume-ml", type=float, default=None)
    parser.add_argument("--strict", action="store_true", help="缺少任一协议节点时退出")
    parser.add_argument("--no-initialize", action="store_true", help="不写入 PLC 侧初始状态")
    parser.add_argument(
        "--cleanup-on-exit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="正常退出时清理仿真器拥有的 PLC 输出",
    )
    return parser.parse_args(argv)


def _config_bool(
    cli_value: Optional[bool],
    config: Dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    return bool(config.get(key, default)) if cli_value is None else bool(cli_value)


def main() -> int:
    args = parse_args()
    config = load_yaml(args.config)
    simulator = SzlabHandshakeSimulator(
        url=args.url,
        namespace_index=args.namespace_index,
        node_prefix=args.node_prefix,
        delay_ms=args.delay_ms if args.delay_ms is not None else int(config.get("delay_ms", 120)),
        poll_ms=args.poll_ms if args.poll_ms is not None else int(config.get("poll_ms", 20)),
        # 显式全局延时覆盖 YAML 的分组延时。设备动作自身的时间参数
        # （如 S04 磁搅时间）仍然优先，delay_ms 只是无时间参数时的仿真延时。
        delays={} if args.delay_ms is not None else dict(config.get("delays", {})),
        initial_values=dict(config.get("initial_values", {})),
        strict=args.strict,
        workflow=args.workflow or str(config.get("workflow", "all")),
        position=(
            args.position
            if args.position is not None
            else int(config.get("position", 1))
        ),
        pump=args.pump if args.pump is not None else int(config.get("pump", 1)),
        s06_robot_workflow=_config_bool(
            args.s06_robot_workflow,
            config,
            "s06_robot_workflow",
            False,
        ),
        s09_pipetting_workflow=_config_bool(
            args.s09_pipetting_workflow,
            config,
            "s09_pipetting_workflow",
            True,
        ),
        s09_remaining_volume_ml=(
            args.s09_remaining_volume_ml
            if args.s09_remaining_volume_ml is not None
            else float(config.get("s09_remaining_volume_ml", 100.0))
        ),
    )
    cleanup_on_exit = _config_bool(
        args.cleanup_on_exit,
        config,
        "cleanup_on_exit",
        True,
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

    initialized = False
    try:
        simulator.connect(timeout=args.connect_timeout)
        initialized = not args.no_initialize
        simulator.run_forever(initialize=initialized)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    finally:
        if initialized and cleanup_on_exit and simulator._connected:
            try:
                simulator.cleanup()
            except Exception as exc:  # noqa: BLE001
                log.warning("清理 PLC 侧握手输出失败：%s", exc)
        simulator.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
