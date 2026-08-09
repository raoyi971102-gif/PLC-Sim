from __future__ import annotations

from typing import Any

import szlab_handshake_agent as handshake


class MemoryAdapter:
    """为新工作流握手切片保存可观察的 PLC 变量值。"""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            handshake.ROBOT_TASK_NUMBER: 0,
            handshake.S04_ROBOT_POSITION: 0,
            handshake.s04_process(1): 0,
            handshake.s04_params_written(1): False,
            handshake.S06_PROCESS: 0,
            handshake.S06_PARAMS_WRITTEN: False,
            handshake.S07_PROCESS: 0,
            handshake.S07_PARAMS_WRITTEN: False,
            handshake.S08_PROCESS: 0,
            handshake.S08_PARAMS_WRITTEN: False,
            handshake.S08_CAP_STORAGE_SLOT: 0,
            handshake.S09_PROCESS: 0,
            handshake.S09_PARAMS_WRITTEN: False,
        }

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def test_attachment_flow_has_an_independent_scan_free_handshake_catalog() -> None:
    """证明附件流程具有独立且无 S07 扫码的握手目录。

    参数：无。
    返回：无；断言动作按新内核源码首次出现顺序登记，旧流程保持可用。
    """

    specs = {
        spec.workflow_id: spec for spec in handshake.build_workflow_specs()
    }
    attachment = specs[handshake.ATTACHMENT_SINGLE_SAMPLE_WORKFLOW]

    assert handshake.SINGLE_SAMPLE_WORKFLOW in specs
    assert len(specs) == 19
    assert attachment.actions == (
        "szlab_mixer_robot.pick",
        "szlab_mixer_robot.place",
        "host_node.transfer_resource",
        "szlab_s08_cap_station.process_liquid_reagent_100ml_cap_with_material",
        "szlab_s07_solid_addition.prepare_powder_cartridge_site",
        "szlab_s07_solid_addition.dose_powder_with_two_materials",
        "szlab_mixer_pump.add_solvent_with_materials",
        "szlab_mixer_pipetting_station.add_liquid_with_materials",
        "szlab_mixer_stirrer.stir_beaker",
        "szlab_s08_cap_station.process_sample_vial_250ml_cap_with_material",
        "szlab_mixer_photoshotting.inspect_beaker",
        "szlab_mixer_robot.pick_beaker",
        "szlab_mixer_robot.pour_beaker_into_vial",
    )
    assert "szlab_s07_solid_addition.scan_powder_cartridges" not in attachment.actions


def test_attachment_flow_completes_s07_prepare_and_dose_without_scan_cycle() -> None:
    """证明新流程可跳过工艺 1，直接完成 S07 准备与加粉握手。

    参数：无。
    返回：无；依次验证工艺 2、3 的 accepted/completed/reset 边沿和动作身份。
    """

    adapter = MemoryAdapter()
    simulator = handshake.WorkflowHandshakeSimulator(
        adapter,
        process_delay=0.5,
        workflow=handshake.ATTACHMENT_SINGLE_SAMPLE_WORKFLOW,
    )
    simulator.initialize()

    expected_actions = {
        2: handshake.S07_MATERIAL_PREPARE_ACTION,
        3: handshake.SINGLE_SAMPLE_S07_DOSE_ACTION,
    }
    clock = 0.0
    for process in (2, 3):
        adapter.write(handshake.S07_PROCESS, process)
        adapter.write(handshake.S07_PARAMS_WRITTEN, True)
        accepted = simulator.step(now=clock)
        completed = simulator.step(now=clock + 0.5)

        assert [(event.action, event.phase) for event in accepted] == [
            (expected_actions[process], "accepted")
        ]
        assert [(event.action, event.phase) for event in completed] == [
            (expected_actions[process], "completed")
        ]
        assert adapter.read(handshake.S07_DONE) == process

        adapter.write(handshake.S07_PROCESS, 0)
        adapter.write(handshake.S07_PARAMS_WRITTEN, False)
        reset = simulator.step(now=clock + 0.6)
        assert [(event.action, event.phase) for event in reset] == [
            (expected_actions[process], "reset")
        ]
        clock += 1.0

    assert simulator.completed_actions == 2
    assert simulator.all_cycles_idle() is True
