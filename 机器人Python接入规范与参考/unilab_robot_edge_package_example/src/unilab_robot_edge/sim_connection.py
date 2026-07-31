from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from .contracts import RobotMotionFailed, RobotRejected, RobotResultUnknown


class SimRobotConnection:
    """离线测试连接；不创建 Socket，不连接机器人。"""

    def __init__(self) -> None:
        self.status: dict[str, Any] = {
            "connection": "ONLINE",
            "control_authority": "REMOTE_AUTO",
            "safety_state": "NORMAL",
            "operational_state": "IDLE",
            "controller_boot_id": "sim-controller-boot-1",
            "calibration_version": "ptlc-cell-calibration@1",
            "tool_profile": "rack-gripper@1",
            "payload_profile": "payload.rack.empty.v1",
            "external_axis_context": {"rail_position": "home"},
        }
        self.permit: dict[str, Any] = {
            "granted": True,
            "source": "sim-cell-controller",
            "observed_at": time.time(),
        }
        self.execute_count = 0
        self.point_execute_count = 0
        self.stop_count = 0
        self.next_outcome = "SUCCEEDED"
        self._observations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def read_status(self) -> Mapping[str, Any]:
        with self._lock:
            return dict(self.status)

    def read_motion_permit(self) -> Mapping[str, Any]:
        with self._lock:
            permit = dict(self.permit)
            if permit.get("auto_refresh", True):
                permit["observed_at"] = time.time()
            return permit

    def execute_skill(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            self.execute_count += 1
        return self._execute(request, kind="skill")

    def execute_point(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            self.point_execute_count += 1
        return self._execute(request, kind="point")

    def controlled_stop(
        self,
        command_id: str,
        reason: str,
    ) -> Mapping[str, Any]:
        with self._lock:
            self.stop_count += 1
            self.status["operational_state"] = "IDLE"
            observation = {
                "confirmed": True,
                "command_id": command_id,
                "reason": reason,
                "observed_at": time.time(),
            }
            self._observations[command_id] = {
                "state": "CANCELED",
                "message": reason,
                "witness": {
                    "controller_command_id": f"sim-{command_id}",
                    "observed_at": observation["observed_at"],
                    "final_state": "IDLE",
                },
            }
            return observation

    def reconcile(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        command_id = str(request["command_id"])
        with self._lock:
            return dict(
                self._observations.get(
                    command_id,
                    {
                        "state": "UNKNOWN",
                        "message": "simulator has no terminal witness",
                    },
                )
            )

    def set_reconcile_observation(
        self,
        command_id: str,
        *,
        state: str,
    ) -> None:
        observation: dict[str, Any] = {"state": state}
        if state == "SUCCEEDED":
            observation["witness"] = {
                "controller_command_id": f"sim-{command_id}",
                "observed_at": time.time(),
                "final_state": "IDLE",
            }
        with self._lock:
            self._observations[command_id] = observation

    def _execute(
        self,
        request: Mapping[str, Any],
        *,
        kind: str,
    ) -> Mapping[str, Any]:
        command_id = str(request["command_id"])
        with self._lock:
            outcome = self.next_outcome
            self.next_outcome = "SUCCEEDED"
            self.status["operational_state"] = "IDLE"
        if outcome == "REJECTED":
            raise RobotRejected(f"simulated {kind} rejection")
        if outcome == "FAILED":
            raise RobotMotionFailed(f"simulated {kind} failure")
        if outcome == "UNKNOWN":
            with self._lock:
                self._observations[command_id] = {
                    "state": "UNKNOWN",
                    "message": "simulated lost reply",
                }
            raise RobotResultUnknown(f"simulated {kind} result unknown")
        observation = {
            "state": "SUCCEEDED",
            "witness": {
                "controller_command_id": f"sim-{command_id}",
                "observed_at": time.time(),
                "final_state": "IDLE",
            },
        }
        with self._lock:
            self._observations[command_id] = dict(observation)
        return observation
