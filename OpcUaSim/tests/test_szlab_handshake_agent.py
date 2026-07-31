from __future__ import annotations

from szlab_handshake_agent import (
    ROBOT_ACTION_BY_TASK,
    S04_ROBOT_POSITION,
    S06_BEAKER_SENSOR,
    S06_PARAMS_WRITTEN,
    S06_PROCESS,
    S071_ROBOT_POSITION,
    S071_SENSOR_BY_SLOT,
    S072_SENSOR_BY_POSITION,
    S07_PARAMS_WRITTEN,
    S07_PROCESS,
    S08_CAP_STORAGE_SLOT,
    S08_PARAMS_WRITTEN,
    S08_PROCESS,
    S09_PARAMS_WRITTEN,
    S09_PROCESS,
    SUPPORTED_ACTIONS,
    WORKFLOW_IDS,
    SzlabHandshakeSimulator,
    parse_args,
    s04_allow,
    s04_done,
    s04_params_written,
    s04_process,
    s04_sensor,
)


class FakeNode:
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


def make_simulator(delay_ms: int = 100) -> tuple[SzlabHandshakeSimulator, dict[str, FakeNode]]:
    simulator = SzlabHandshakeSimulator(
        "opc.tcp://unused",
        delay_ms=delay_ms,
        s06_robot_workflow=True,
        s09_pipetting_workflow=True,
    )
    nodes = {name: FakeNode(0) for name in simulator.required_names}
    simulator.nodes = nodes
    simulator._prepare_capabilities()
    simulator.initialize()
    return simulator, nodes


def run_robot_task(
    simulator: SzlabHandshakeSimulator,
    nodes: dict[str, FakeNode],
    task: int,
    started_at: float,
) -> None:
    nodes["任务号"].value = task
    nodes["Robot_任务写入完成"].value = True
    simulator.tick(now=started_at)
    simulator.tick(now=started_at + 0.11)


def reset_robot(
    simulator: SzlabHandshakeSimulator,
    nodes: dict[str, FakeNode],
    now: float,
) -> None:
    nodes["Robot_任务写入完成"].value = False
    simulator.tick(now=now)


def test_protocol_catalog_matches_reference_handshaker():
    assert len(SUPPORTED_ACTIONS) == 19
    assert len(WORKFLOW_IDS) == 12
    assert set(ROBOT_ACTION_BY_TASK) == {7, 8, 11, 12, 13, 15, 16}
    simulator, nodes = make_simulator()
    assert nodes["Robot_任务写入完成"].value is False


def test_robot_s04_place_and_pick_update_sensor_and_reset_with_retained_task():
    simulator, nodes = make_simulator()
    nodes[S04_ROBOT_POSITION].value = 2

    run_robot_task(simulator, nodes, task=7, started_at=1.0)
    assert nodes["Robot_任务允许写入"].value is False
    assert nodes["Robot_Home"].value is True
    assert nodes["Robot_任务完成"].value == 7
    assert nodes[s04_sensor(2)].value is True

    # 任务号允许保留旧值，write_done=False 才是完成码已消费的确认。
    reset_robot(simulator, nodes, now=1.12)
    assert nodes["任务号"].value == 7
    assert nodes["Robot_任务完成"].value == 0
    assert nodes["Robot_任务允许写入"].value is True

    run_robot_task(simulator, nodes, task=8, started_at=1.2)
    assert nodes["Robot_任务完成"].value == 8
    assert nodes[s04_sensor(2)].value is False


def test_selected_workflow_limits_nodes_initialization_and_polling():
    simulator = SzlabHandshakeSimulator(
        "opc.tcp://unused",
        workflow="szlab_magnetic_stirring_workflow",
        position=4,
    )

    assert simulator.enabled_components == frozenset({"s04"})
    assert simulator.enabled_robot_tasks == frozenset()
    assert simulator.s04_positions == frozenset({4})
    assert simulator.initial_values == {
        s04_allow(4): True,
        s04_done(4): False,
    }
    assert simulator.required_names == {
        s04_allow(4),
        s04_process(4),
        s04_params_written(4),
        s04_done(4),
    }

    simulator.nodes = {name: FakeNode(0) for name in simulator.required_names}
    simulator._prepare_capabilities()
    simulator.initialize()
    assert simulator.enabled_groups == {"s04"}
    assert simulator.enabled_s04_positions == {4}


def test_selected_robot_workflow_ignores_other_workflow_tasks():
    simulator = SzlabHandshakeSimulator(
        "opc.tcp://unused",
        workflow="s06_robot_workflow",
        delay_ms=0,
    )
    simulator.nodes = {name: FakeNode(0) for name in simulator.required_names}
    simulator._prepare_capabilities()
    simulator.initialize()

    assert simulator.enabled_robot_tasks == frozenset({11, 12})
    assert simulator.s06_robot_workflow is True
    assert simulator.nodes[S06_BEAKER_SENSOR].value is False

    simulator.nodes["任务号"].value = 7
    simulator.nodes["Robot_任务写入完成"].value = True
    assert simulator.tick(now=1.0) == []
    assert simulator.nodes["Robot_任务允许写入"].value is True

    simulator.nodes["任务号"].value = 11
    accepted = simulator.tick(now=1.1)
    assert [(event.details["task_number"], event.phase) for event in accepted] == [
        (11, "accepted")
    ]


def test_cli_accepts_workflow_debug_parameters():
    args = parse_args(
        [
            "--workflow",
            "s04_robot_stirring_workflow",
            "--position",
            "5",
            "--pump",
            "3",
            "--delay-ms",
            "250",
            "--poll-ms",
            "40",
            "--s09-remaining-volume-ml",
            "88.5",
        ]
    )

    assert args.workflow == "s04_robot_stirring_workflow"
    assert args.position == 5
    assert args.pump == 3
    assert args.delay_ms == 250
    assert args.poll_ms == 40
    assert args.s09_remaining_volume_ml == 88.5


def test_s06_robot_place_pick_and_process_reset_with_selector_retained():
    simulator, nodes = make_simulator()
    assert nodes[S06_BEAKER_SENSOR].value is False

    run_robot_task(simulator, nodes, task=11, started_at=2.0)
    assert nodes[S06_BEAKER_SENSOR].value is True
    reset_robot(simulator, nodes, now=2.12)

    nodes[S06_PROCESS].value = 2
    nodes[S06_PARAMS_WRITTEN].value = True
    simulator.tick(now=2.2)
    simulator.tick(now=2.31)
    assert nodes["S06加工完成"].value is True
    assert nodes["S06允许加工"].value is False

    # S06 与通用规则不同：参数标志下降即可复位，工艺号可保留。
    nodes[S06_PARAMS_WRITTEN].value = False
    simulator.tick(now=2.32)
    assert nodes[S06_PROCESS].value == 2
    assert nodes["S06加工完成"].value is False
    assert nodes["S06允许加工"].value is True

    run_robot_task(simulator, nodes, task=12, started_at=2.4)
    assert nodes[S06_BEAKER_SENSOR].value is False


def test_s09_accepts_persistent_process_even_if_parameter_pulse_was_missed():
    simulator, nodes = make_simulator()
    nodes[S09_PROCESS].value = 5
    nodes[S09_PARAMS_WRITTEN].value = False

    accepted = simulator.tick(now=3.0)
    assert accepted[0].details["params_written"] is False
    simulator.tick(now=3.11)
    assert nodes["S09工艺完成"].value == 5

    # Edge 可直接切到下一工艺；先复位上一轮，再在下一次轮询接新单。
    nodes[S09_PROCESS].value = 7
    simulator.tick(now=3.12)
    assert nodes["S09工艺完成"].value == 0
    assert nodes["S09允许加工"].value is True
    simulator.tick(now=3.13)
    assert nodes["S09允许加工"].value is False
    simulator.tick(now=3.24)
    assert nodes["S09工艺完成"].value == 7


def test_s071_and_s072_robot_sequence_rearms_powder_slot():
    simulator, nodes = make_simulator()
    nodes[S071_ROBOT_POSITION].value = 1

    run_robot_task(simulator, nodes, task=13, started_at=4.0)
    assert nodes[S071_SENSOR_BY_SLOT[1]].value is True
    reset_robot(simulator, nodes, now=4.12)

    run_robot_task(simulator, nodes, task=15, started_at=4.2)
    assert nodes[S072_SENSOR_BY_POSITION[1]].value is True
    reset_robot(simulator, nodes, now=4.32)

    run_robot_task(simulator, nodes, task=16, started_at=4.4)
    assert nodes[S072_SENSOR_BY_POSITION[1]].value is False
    assert nodes[S071_SENSOR_BY_SLOT[1]].value is False


def test_s04_and_s07_require_their_full_reset_conditions():
    simulator, nodes = make_simulator()

    nodes[s04_process(1)].value = 3
    nodes[s04_params_written(1)].value = True
    simulator.tick(now=5.0)
    simulator.tick(now=5.11)
    assert nodes["S041加工完成"].value is True

    nodes[s04_params_written(1)].value = False
    simulator.tick(now=5.12)
    assert nodes["S041加工完成"].value is True
    nodes[s04_process(1)].value = 0
    simulator.tick(now=5.13)
    assert nodes["S041加工完成"].value is False
    assert nodes["S041允许加工"].value is True

    for index, process in enumerate((1, 2, 3)):
        started_at = 6.0 + index
        nodes[S07_PROCESS].value = process
        nodes[S07_PARAMS_WRITTEN].value = True
        simulator.tick(now=started_at)
        simulator.tick(now=started_at + 0.11)
        assert nodes["S07工艺完成"].value == process
        nodes[S07_PARAMS_WRITTEN].value = False
        nodes[S07_PROCESS].value = 0
        simulator.tick(now=started_at + 0.12)
        assert nodes["S07工艺完成"].value == 0


def test_s08_waits_for_process_parameters_and_cap_slot_to_reset():
    simulator, nodes = make_simulator()
    nodes[S08_PROCESS].value = 5
    nodes[S08_PARAMS_WRITTEN].value = True
    nodes[S08_CAP_STORAGE_SLOT].value = 1
    simulator.tick(now=9.0)
    simulator.tick(now=9.11)
    assert nodes["S08工艺完成"].value == 5

    nodes[S08_PARAMS_WRITTEN].value = False
    nodes[S08_PROCESS].value = 0
    simulator.tick(now=9.12)
    assert nodes["S08工艺完成"].value == 5
    nodes[S08_CAP_STORAGE_SLOT].value = 0
    simulator.tick(now=9.13)
    assert nodes["S08工艺完成"].value == 0
    assert nodes["S08允许加工"].value is True


def test_cleanup_only_resets_simulator_outputs():
    simulator, nodes = make_simulator()
    nodes["任务号"].value = 13
    nodes["Robot_任务写入完成"].value = True
    nodes[S07_PROCESS].value = 3
    nodes[S07_PARAMS_WRITTEN].value = True

    simulator.cleanup()

    assert nodes["任务号"].value == 13
    assert nodes["Robot_任务写入完成"].value is True
    assert nodes[S07_PROCESS].value == 3
    assert nodes[S07_PARAMS_WRITTEN].value is True
    assert nodes["Robot_Home"].value is False
    assert nodes["Robot_任务允许写入"].value is False
