from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import yaml
from unilab_robot_edge.driver import RobotEdgeDriver
from unilab_robot_edge.sim_connection import SimRobotConnection

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _driver(
    tmp_path: Path,
    *,
    connection: SimRobotConnection | None = None,
    approved_points: bool = False,
) -> tuple[RobotEdgeDriver, SimRobotConnection]:
    profile = yaml.safe_load(
        (PACKAGE_ROOT / "src/unilab_robot_edge/profile/package.yaml").read_text(
            encoding="utf-8"
        )
    )
    config = deepcopy(profile["driver_config"])
    config["journal_path"] = str(tmp_path / "commands.sqlite3")
    if approved_points:
        point_path = tmp_path / "points.yaml"
        points = yaml.safe_load(
            (
                PACKAGE_ROOT / "src/unilab_robot_edge/config/points.example.yaml"
            ).read_text(encoding="utf-8")
        )
        points["point_set"]["approved"] = True
        for point in points["points"].values():
            point["validation_status"] = "validated"
        point_path.write_text(
            yaml.safe_dump(points, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        config["point_catalog"] = {"path": str(point_path)}
    active_connection = connection or SimRobotConnection()
    return (
        RobotEdgeDriver(plc=active_connection, driver_config=config),
        active_connection,
    )


def _resource() -> dict[str, object]:
    return {
        "id": "rack-01",
        "resource_type": "rack",
        "revision": 7,
        "location": {
            "parent_id": "collector-warehouse",
            "site": "site-01",
        },
    }


def _pick_inputs(
    command_id: str = "cmd-1",
    sequence: int = 1,
) -> dict[str, object]:
    return {
        "resource": _resource(),
        "command_id": command_id,
        "source_boot_id": "os-boot-1",
        "monotonic_sequence": sequence,
        "parameters": {},
    }


def test_same_command_is_not_dispatched_twice(tmp_path: Path) -> None:
    driver, connection = _driver(tmp_path)

    first = asyncio.run(driver.run_macro("pick", inputs=_pick_inputs()))
    second = asyncio.run(driver.run_macro("pick", inputs=_pick_inputs()))

    assert first.terminal == "succeeded"
    assert second.terminal == "succeeded"
    assert connection.execute_count == 1


def test_same_command_with_different_request_is_rejected(tmp_path: Path) -> None:
    driver, connection = _driver(tmp_path)
    first = _pick_inputs()
    second = _pick_inputs()
    second["parameters"] = {"vision_dx_mm": 1.0}

    assert asyncio.run(driver.run_macro("pick", inputs=first)).terminal == "succeeded"
    conflict = asyncio.run(driver.run_macro("pick", inputs=second))

    assert conflict.terminal == "failed"
    assert conflict.physical_state == "not_started"
    assert connection.execute_count == 1


def test_missing_permit_rejects_before_dispatch(tmp_path: Path) -> None:
    connection = SimRobotConnection()
    connection.permit["granted"] = False
    driver, connection = _driver(tmp_path, connection=connection)

    result = asyncio.run(driver.run_macro("pick", inputs=_pick_inputs()))

    assert result.terminal == "failed"
    assert result.outputs["state"] == "REJECTED"
    assert result.physical_state == "not_started"
    assert connection.execute_count == 0


def test_unknown_is_not_retried_and_can_be_reconciled(tmp_path: Path) -> None:
    connection = SimRobotConnection()
    connection.next_outcome = "UNKNOWN"
    driver, connection = _driver(tmp_path, connection=connection)
    inputs = _pick_inputs()

    unknown = asyncio.run(driver.run_macro("pick", inputs=inputs))
    repeated = asyncio.run(driver.run_macro("pick", inputs=inputs))

    assert unknown.outputs["state"] == "UNKNOWN"
    assert unknown.reconcile_required is True
    assert repeated.outputs["state"] == "UNKNOWN"
    assert connection.execute_count == 1

    blocked = asyncio.run(
        driver.run_macro("pick", inputs=_pick_inputs("cmd-2", sequence=2))
    )
    assert blocked.terminal == "failed"
    assert blocked.physical_state == "not_started"
    assert connection.execute_count == 1

    connection.set_reconcile_observation("cmd-1", state="SUCCEEDED")
    reconciled = asyncio.run(
        driver.run_macro("reconcile", inputs={"command_id": "cmd-1"})
    )

    assert reconciled.terminal == "succeeded"
    assert reconciled.outputs["state"] == "SUCCEEDED"
    assert connection.execute_count == 1


def test_stop_does_not_rewrite_completed_command(tmp_path: Path) -> None:
    driver, connection = _driver(tmp_path)
    completed = asyncio.run(driver.run_macro("pick", inputs=_pick_inputs()))
    assert completed.terminal == "succeeded"

    stopped = asyncio.run(
        driver.run_macro(
            "request_controlled_stop",
            inputs={"command_id": "cmd-1", "reason": "late request"},
        )
    )

    assert stopped.terminal == "succeeded"
    assert stopped.outputs["state"] == "SUCCEEDED"
    assert connection.stop_count == 0


def test_sequence_regression_is_rejected(tmp_path: Path) -> None:
    driver, connection = _driver(tmp_path)

    first = asyncio.run(
        driver.run_macro("pick", inputs=_pick_inputs("cmd-2", sequence=2))
    )
    stale = asyncio.run(
        driver.run_macro("pick", inputs=_pick_inputs("cmd-1", sequence=1))
    )

    assert first.terminal == "succeeded"
    assert stale.terminal == "failed"
    assert stale.outputs["state"] == "REJECTED"
    assert connection.execute_count == 1


def test_draft_point_set_cannot_start_commissioning(tmp_path: Path) -> None:
    driver, _ = _driver(tmp_path)

    result = asyncio.run(
        driver.run_macro(
            "begin_commissioning",
            inputs={
                "session_id": "session-1",
                "point_set_version": "ptlc-cell@2026-07-31",
                "calibration_version": "ptlc-cell-calibration@1",
                "tool_profile": "rack-gripper@1",
                "payload_profile": "payload.rack.empty.v1",
                "external_axis_context": {"rail_position": "home"},
                "speed_cap_percent": 5,
                "expires_in_s": 300,
            },
        )
    )

    assert result.terminal == "failed"
    assert result.physical_state == "not_started"


def test_commissioning_freezes_context_and_moves_named_point(tmp_path: Path) -> None:
    driver, connection = _driver(tmp_path, approved_points=True)
    begin = asyncio.run(
        driver.run_macro(
            "begin_commissioning",
            inputs={
                "session_id": "session-1",
                "point_set_version": "ptlc-cell@2026-07-31",
                "calibration_version": "ptlc-cell-calibration@1",
                "tool_profile": "rack-gripper@1",
                "payload_profile": "payload.rack.empty.v1",
                "external_axis_context": {"rail_position": "home"},
                "speed_cap_percent": 5,
                "expires_in_s": 300,
            },
        )
    )
    assert begin.terminal == "succeeded"

    moved = asyncio.run(
        driver.run_macro(
            "move_to_point",
            inputs={
                "session_id": "session-1",
                "point_ref": "commissioning.safe-a",
                "command_id": "point-cmd-1",
                "source_boot_id": "os-boot-1",
                "monotonic_sequence": 1,
                "motion": "move_j",
                "speed_percent": 5,
                "offset": {
                    "frame": "site",
                    "translation_mm": [0, 0, 1],
                    "rotation_deg": [0, 0, 0],
                },
            },
        )
    )

    assert moved.terminal == "succeeded"
    assert connection.point_execute_count == 1

    connection.status["controller_boot_id"] = "sim-controller-boot-2"
    after_restart = asyncio.run(
        driver.run_macro(
            "move_to_point",
            inputs={
                "session_id": "session-1",
                "point_ref": "commissioning.safe-b",
                "command_id": "point-cmd-2",
                "source_boot_id": "os-boot-1",
                "monotonic_sequence": 2,
            },
        )
    )
    assert after_restart.terminal == "failed"
    assert after_restart.physical_state == "not_started"
    assert connection.point_execute_count == 1
