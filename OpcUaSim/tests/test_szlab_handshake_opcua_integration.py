from __future__ import annotations

import socket
from typing import Any

from opcua import ua

from common import NodeDef
from server import add_nodes, build_server, register_ns_padding
from szlab_handshake_agent import (
    ROBOT_TASK_NUMBER,
    ROBOT_WRITE_DONE,
    S04_ROBOT_POSITION,
    S09_PARAMS_WRITTEN,
    S09_PROCESS,
    S09_WORKFLOW,
    OpcUaVariableAdapter,
    WorkflowHandshakeSimulator,
    s04_sensor,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, float):
        return "FLOAT"
    return "INT32"


def _definitions(values: dict[str, Any]) -> list[NodeDef]:
    return [
        NodeDef(
            name,
            "",
            "VARIABLE",
            _data_type(value),
            f"ns=4;s=上位机通讯|{name}",
        )
        for name, value in values.items()
    ]


def _start_server(values: dict[str, Any]):
    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    server = build_server(endpoint)
    namespace_index = register_ns_padding(server, 4, "urn:szlab:test")
    nodes = add_nodes(server, namespace_index, _definitions(values))
    server.start()
    return server, nodes, endpoint


def test_robot_handshake_through_real_opcua_adapter() -> None:
    seed = {
        ROBOT_TASK_NUMBER: 0,
        S04_ROBOT_POSITION: 0,
    }
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow="szlab_robot_action_workflow",
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow="szlab_robot_action_workflow",
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        nodes[S04_ROBOT_POSITION].set_value(ua.Variant(1, ua.VariantType.Int32))
        nodes[ROBOT_TASK_NUMBER].set_value(ua.Variant(7, ua.VariantType.Int32))
        nodes[ROBOT_WRITE_DONE].set_value(ua.Variant(True, ua.VariantType.Boolean))

        events = simulator.step(now=1.0) + simulator.step(now=1.0)

        assert [(event.phase, event.detail["task_number"]) for event in events] == [
            ("accepted", 7),
            ("completed", 7),
        ]
        assert nodes["Robot_任务完成"].get_value() == 7
        assert nodes["Robot_任务允许写入"].get_value() is False
        assert nodes[s04_sensor(1)].get_value() is True

        nodes[ROBOT_WRITE_DONE].set_value(ua.Variant(False, ua.VariantType.Boolean))
        simulator.step(now=1.01)
        assert nodes["Robot_任务完成"].get_value() == 0
        assert nodes["Robot_任务允许写入"].get_value() is True
    finally:
        adapter.disconnect()
        server.stop()


def test_latest_s09_cycle_requires_official_parameter_reset() -> None:
    seed = {S09_PROCESS: 0, S09_PARAMS_WRITTEN: False}
    blueprint = WorkflowHandshakeSimulator(
        _MemoryAdapter(seed),
        workflow=S09_WORKFLOW,
        process_delay=0.0,
    )
    values = {**blueprint.initialization_values(), **seed}
    server, nodes, endpoint = _start_server(values)
    adapter = OpcUaVariableAdapter(endpoint, "ns=4;s=上位机通讯|")
    simulator = WorkflowHandshakeSimulator(
        adapter,
        workflow=S09_WORKFLOW,
        process_delay=0.0,
    )
    try:
        adapter.connect()
        simulator.initialize()
        nodes[S09_PROCESS].set_value(ua.Variant(5, ua.VariantType.Int32))
        nodes[S09_PARAMS_WRITTEN].set_value(ua.Variant(True, ua.VariantType.Boolean))

        events = simulator.step(now=2.0) + simulator.step(now=2.0)

        assert [(event.phase, event.detail["process"]) for event in events] == [
            ("accepted", 5),
            ("completed", 5),
        ]
        assert nodes["S09工艺完成"].get_value() == 5

        nodes[S09_PARAMS_WRITTEN].set_value(ua.Variant(False, ua.VariantType.Boolean))
        assert simulator.step(now=2.01) == []
        nodes[S09_PROCESS].set_value(ua.Variant(0, ua.VariantType.Int32))
        reset = simulator.step(now=2.02)
        assert [(event.phase, event.detail["process"]) for event in reset] == [
            ("reset", 5)
        ]
        assert nodes["S09工艺完成"].get_value() == 0
        assert nodes["S09允许加工"].get_value() is True
    finally:
        adapter.disconnect()
        server.stop()
