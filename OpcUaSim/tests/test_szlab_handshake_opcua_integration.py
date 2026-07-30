from __future__ import annotations

import socket

from opcua import ua

from common import NodeDef
from server import add_nodes, build_server, register_ns_padding
from szlab_handshake_agent import SzlabHandshakeSimulator


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_robot_handshake_against_real_opcua_server():
    port = _free_port()
    endpoint = f"opc.tcp://127.0.0.1:{port}/xuse_sim/"
    definitions = [
        NodeDef("Robot_Home", "", "VARIABLE", "BOOLEAN", "ns=4;s=上位机通讯|Robot_Home"),
        NodeDef(
            "Robot_任务允许写入",
            "",
            "VARIABLE",
            "BOOLEAN",
            "ns=4;s=上位机通讯|Robot_任务允许写入",
        ),
        NodeDef(
            "Robot_任务写入完成",
            "",
            "VARIABLE",
            "BOOLEAN",
            "ns=4;s=上位机通讯|Robot_任务写入完成",
        ),
        NodeDef("任务号", "", "VARIABLE", "INT32", "ns=4;s=上位机通讯|任务号"),
        NodeDef(
            "Robot_任务完成",
            "",
            "VARIABLE",
            "INT32",
            "ns=4;s=上位机通讯|Robot_任务完成",
        ),
    ]
    server = build_server(endpoint)
    namespace_index = register_ns_padding(server, 4, "urn:szlab:test")
    nodes = add_nodes(server, namespace_index, definitions)
    server.start()
    simulator = SzlabHandshakeSimulator(endpoint, delay_ms=0)
    try:
        simulator.connect(timeout=3.0)
        simulator.initialize()

        nodes["任务号"].set_value(ua.Variant(17, ua.VariantType.Int32))
        nodes["Robot_任务写入完成"].set_value(
            ua.Variant(True, ua.VariantType.Boolean)
        )
        simulator.tick(now=1.0)
        simulator.tick(now=1.01)

        assert nodes["Robot_任务完成"].get_value() == 17
        assert nodes["Robot_任务允许写入"].get_value() is False

        nodes["Robot_任务写入完成"].set_value(
            ua.Variant(False, ua.VariantType.Boolean)
        )
        nodes["任务号"].set_value(ua.Variant(0, ua.VariantType.Int32))
        simulator.tick(now=1.02)

        assert nodes["Robot_任务完成"].get_value() == 0
        assert nodes["Robot_任务允许写入"].get_value() is True
    finally:
        simulator.disconnect()
        server.stop()
