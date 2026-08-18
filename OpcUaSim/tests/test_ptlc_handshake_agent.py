from __future__ import annotations

from typing import Any

import pytest

from ptlc_handshake_agent import OUTPUT_DEFAULTS, PtlcHandshakeSimulator


class MemoryAdapter:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def read(self, name: str) -> Any:
        return self.values[name]

    def write(self, name: str, value: Any) -> None:
        self.values[name] = value


def _station_values(station: str) -> dict[str, Any]:
    values = {
        "PLC_Ready": True,
        "PLC_Deploy_State": 0,
        f"{station}_L2_ActionCode": 0,
        f"{station}_L2_RequestSeq": 0,
        f"{station}_L2_Start": False,
        f"{station}_L2_Reset": False,
    }
    values.update({f"{station}_L2_{key}": value for key, value in OUTPUT_DEFAULTS.items()})
    return values


def test_l2_cycle_accepts_completes_applies_rail_effect_and_rearms() -> None:
    values = {
        **_station_values("Rail"),
        "Rail_Target_Position": 2,
        "Rail_Current_Position": 0,
        "Rail_Pos_Target": [10.0, 25.5, 40.0],
        "Rail_ActPos": 0.0,
    }
    config = {
        "stations": ["Rail"],
        "station_effects": {
            "Rail": {
                "copy": [{"from": "Rail_Target_Position", "to": "Rail_Current_Position"}],
                "indexed_copy": [{
                    "from": "Rail_Pos_Target", "index": "Rail_Target_Position",
                    "index_base": 1, "to": "Rail_ActPos",
                }],
            }
        },
    }
    adapter = MemoryAdapter(values)
    sim = PtlcHandshakeSimulator(adapter, config=config, delay_s=0.1)
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 10,
        "Rail_L2_RequestSeq": 17,
        "Rail_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=1.0)] == ["accepted"]
    assert values["Rail_L2_State"] == 10
    assert [event.phase for event in sim.step(now=1.1)] == ["completed"]
    assert values["Rail_L2_State"] == 20
    assert values["Rail_L2_CompletedSeq"] == 17
    assert values["Rail_Current_Position"] == 2
    assert values["Rail_ActPos"] == 25.5

    values["Rail_L2_Start"] = False
    assert [event.phase for event in sim.step(now=1.2)] == ["rearmed"]
    assert values["Rail_L2_State"] == 0


def test_develop_action_50_updates_true_array_slot() -> None:
    values = {
        **_station_values("Develop"),
        "Expand_Target_Tank": 3,
        "Tank_State": [0] * 8,
        "Tank_Drain_Done": [False] * 8,
    }
    config = {
        "stations": ["Develop"],
        "action_effects": {
            "Develop": {"50": {"set_index": [
                {"node": "Tank_State", "index": "Expand_Target_Tank", "index_base": 1, "value": 98},
                {"node": "Tank_Drain_Done", "index": "Expand_Target_Tank", "index_base": 1, "value": True},
            ]}}
        },
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Develop_L2_ActionCode": 50,
        "Develop_L2_RequestSeq": 4,
        "Develop_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=2.0)] == ["accepted", "completed"]
    assert values["Tank_State"][2] == 98
    assert values["Tank_Drain_Done"][2] is True


def test_fault_injection_rejects_and_reset_returns_idle() -> None:
    values = _station_values("Pump")
    config = {
        "stations": ["Pump"],
        "faults": {"Pump": {"reject_codes": [10]}},
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Pump_L2_ActionCode": 10,
        "Pump_L2_RequestSeq": 8,
        "Pump_L2_Start": True,
    })

    assert [event.phase for event in sim.step(now=3.0)] == ["accepted", "rejected"]
    assert values["Pump_L2_State"] == 30
    assert values["Pump_L2_Retryable"] is True
    assert values["Pump_L2_ErrorCode"] == 102

    values["Pump_L2_Reset"] = True
    assert [event.phase for event in sim.step(now=3.1)] == ["reset"]
    assert values["Pump_L2_State"] == 0


def test_global_ready_gate_rejects_with_protocol_error_190() -> None:
    values = _station_values("Sampling")
    values.update({
        "PLC_Ready": False,
        "Sampling_L2_ActionCode": 40,
        "Sampling_L2_RequestSeq": 3,
        "Sampling_L2_Start": True,
    })
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Sampling"]}, delay_s=0
    )
    sim.initialize()
    assert values["PLC_Ready"] is False
    assert [event.phase for event in sim.step(now=4.0)] == ["accepted", "rejected"]
    assert values["Sampling_L2_ErrorCode"] == 190
    assert values["Sampling_L2_Retryable"] is True


def test_rail_position_gate_rejects_invalid_index_before_motion() -> None:
    values = {
        **_station_values("Rail"),
        "Rail_Target_Position": 9,
        "Rail_Pos_Target": [1.0, 2.0],
        "Rail_ActPos": 0.0,
    }
    config = {
        "stations": ["Rail"],
        "station_effects": {"Rail": {"indexed_copy": [{
            "from": "Rail_Pos_Target", "index": "Rail_Target_Position",
            "index_base": 1, "to": "Rail_ActPos",
        }]}},
    }
    sim = PtlcHandshakeSimulator(MemoryAdapter(values), config=config, delay_s=0)
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 10,
        "Rail_L2_RequestSeq": 7,
        "Rail_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=5.0)] == ["accepted", "rejected"]
    assert values["Rail_L2_State"] == 30
    assert values["Rail_L2_ErrorCode"] == 101


def test_unknown_action_is_rejected_with_dispatcher_error_101() -> None:
    values = _station_values("Rail")
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Rail"]}, delay_s=0
    )
    sim.initialize()
    values.update({
        "Rail_L2_ActionCode": 999,
        "Rail_L2_RequestSeq": 12,
        "Rail_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=1.0)] == ["accepted", "rejected"]
    assert values["Rail_L2_ErrorCode"] == 101
    assert values["Rail_L2_Retryable"] is True


def test_runtime_fault_can_interrupt_a_valid_action() -> None:
    values = _station_values("Pump")
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Pump"]}, delay_s=0
    )
    sim.runtime_faults.set("Pump", 10, "interrupt")
    sim.initialize()
    values.update({
        "Pump_L2_ActionCode": 10,
        "Pump_L2_RequestSeq": 13,
        "Pump_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=2.0)] == ["accepted", "interrupted"]
    assert values["Pump_L2_State"] == 50
    assert values["Pump_L2_ErrorCode"] == 202


def test_deploy_fsm_prepares_commits_and_requires_fail_closed_reset() -> None:
    values = {
        **_station_values("Rail"),
        "PLC_Axis_CommOperational": [True] * 11,
        "PLC_Deploy_RequestSeq": 7,
        "PLC_Deploy_CommitSeq": 0,
        "PLC_Deploy_Start": True,
        "PLC_Deploy_Reset": False,
        "PLC_Deploy_AcceptedSeq": 0,
        "PLC_Deploy_ErrorCode": 0,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={"stations": ["Rail"], "deploy_prepare_ms": 20},
        delay_s=0,
    )
    sim.initialize()
    sim.step(now=10.0)
    assert values["PLC_Deploy_State"] == 10
    assert values["PLC_Deploy_AcceptedSeq"] == 7
    sim.step(now=10.02)
    assert values["PLC_Deploy_State"] == 20
    values["PLC_Deploy_CommitSeq"] = 7
    sim.step(now=10.03)
    assert values["PLC_Deploy_State"] == 25
    values["PLC_Axis_CommOperational"][3] = False
    sim.step(now=10.04)
    assert values["PLC_Deploy_State"] == 25
    assert values["PLC_Deploy_ErrorCode"] == 5
    values.update({
        "PLC_Deploy_Start": False,
        "PLC_Deploy_CommitSeq": 0,
        "PLC_Deploy_Reset": True,
    })
    sim.step(now=10.05)
    assert values["PLC_Deploy_State"] == 0


def test_motion_progresses_continuously_before_done() -> None:
    values = {
        **_station_values("Sampling"),
        "Sampling_4X_Target": 10.0,
        "Sampling_4X_ActPos": 0.0,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values),
        config={
            "stations": ["Sampling"],
            "motion_speed": {"Sampling": 10.0},
            "motion_effects": {"Sampling": [{
                "from": "Sampling_4X_Target", "to": "Sampling_4X_ActPos",
            }]},
        },
        delay_s=0,
    )
    sim.initialize()
    values.update({
        "Sampling_L2_ActionCode": 10,
        "Sampling_L2_RequestSeq": 20,
        "Sampling_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=0.0)] == ["accepted"]
    assert sim.step(now=0.5) == []
    assert values["Sampling_4X_ActPos"] == pytest.approx(5.0)
    assert [event.phase for event in sim.step(now=1.0)] == ["completed"]
    assert values["Sampling_4X_ActPos"] == pytest.approx(10.0)


def test_tank_drain_runs_phases_and_updates_native_array() -> None:
    values = {
        **_station_values("Develop"),
        "Expand_Target_Tank": 2,
        "Tank_State": [0] * 8,
        "Tank_Drain_Enable": [False] * 8,
        "Tank_Drain_Done": [False] * 8,
        "Tank_Drain_CapHit": [False] * 8,
        "Tank_Drain_S": 0.2,
        "Tank_Drain_Cap_S": 0.5,
        "Tank_Blow_S": 0.1,
        "Tank_Dry_S": 0.1,
    }
    sim = PtlcHandshakeSimulator(
        MemoryAdapter(values), config={"stations": ["Develop"]}, delay_s=0
    )
    sim.initialize()
    values.update({
        "Develop_L2_ActionCode": 50,
        "Develop_L2_RequestSeq": 21,
        "Develop_L2_Start": True,
    })
    assert [event.phase for event in sim.step(now=0.0)] == ["accepted"]
    assert values["Tank_State"][1] == 50
    sim.step(now=0.25)
    assert values["Tank_State"][1] == 55
    sim.step(now=0.35)
    assert values["Tank_State"][1] == 56
    assert [event.phase for event in sim.step(now=0.4)] == ["completed"]
    assert values["Tank_State"][1] == 98
    assert values["Tank_Drain_Enable"][1] is False
    assert values["Tank_Drain_Done"][1] is True
