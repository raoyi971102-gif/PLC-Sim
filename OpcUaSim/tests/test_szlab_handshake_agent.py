from __future__ import annotations

from szlab_handshake_agent import RuleState, SzlabHandshakeSimulator, build_rules


class FakeNode:
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


def make_simulator(rule_key: str) -> tuple[SzlabHandshakeSimulator, dict[str, FakeNode]]:
    rule = next(rule for rule in build_rules() if rule.key == rule_key)
    values = {
        rule.trigger: False,
        rule.completion: False if rule.result_kind == "bool" else 0,
        rule.selector: 0,
    }
    if rule.ready_node:
        values[rule.ready_node] = True
    nodes = {name: FakeNode(value) for name, value in values.items()}
    simulator = SzlabHandshakeSimulator("opc.tcp://unused", delay_ms=100)
    simulator.nodes = nodes
    simulator.enabled_rules = [rule]
    simulator.states = {rule.key: RuleState(previous_trigger=False)}
    return simulator, nodes


def test_robot_handshake_returns_task_number_and_reopens_write_gate():
    simulator, nodes = make_simulator("robot")
    nodes["任务号"].value = 17
    nodes["Robot_任务写入完成"].value = True

    simulator.tick(now=1.0)
    assert nodes["Robot_任务允许写入"].value is False
    assert nodes["Robot_任务完成"].value == 0

    simulator.tick(now=1.11)
    assert nodes["Robot_任务完成"].value == 17

    nodes["Robot_任务写入完成"].value = False
    nodes["任务号"].value = 0
    simulator.tick(now=1.12)
    assert nodes["Robot_任务完成"].value == 0
    assert nodes["Robot_任务允许写入"].value is True


def test_s09_short_trigger_pulse_still_completes_selected_process():
    simulator, nodes = make_simulator("s09")
    nodes["S09工艺选择"].value = 9
    nodes["S09参数写入完成"].value = True
    simulator.tick(now=2.0)

    nodes["S09参数写入完成"].value = False
    simulator.tick(now=2.05)
    assert nodes["S09工艺完成"].value == 0

    simulator.tick(now=2.11)
    assert nodes["S09工艺完成"].value == 9

    nodes["S09工艺选择"].value = 0
    simulator.tick(now=2.12)
    assert nodes["S09工艺完成"].value == 0


def test_s04_boolean_completion_resets_after_driver_clears_selector():
    simulator, nodes = make_simulator("s04:1")
    nodes["S041磁搅工艺选择"].value = 3
    nodes["S041参数写入完成"].value = True
    simulator.tick(now=3.0)
    simulator.tick(now=3.11)
    assert nodes["S041加工完成"].value is True

    nodes["S041参数写入完成"].value = False
    nodes["S041磁搅工艺选择"].value = 0
    simulator.tick(now=3.12)
    assert nodes["S041加工完成"].value is False
