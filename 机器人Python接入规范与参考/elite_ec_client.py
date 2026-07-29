"""艾利特 EC/EA 8055 JSON-RPC 最小客户端。

字段以 EC&EA SDK Socket 手册为准。不同控制器版本可能增加或调整运动参数；
投产前必须用与控制器版本一致的手册和仿真/低速真机验证。
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from common import (
    MOTION_CONFIRM_TEXT,
    in_closed_range,
    require_motion_confirmation,
    vector6,
)


class EliteEcError(RuntimeError):
    pass


class EliteEcClient:
    def __init__(self, host: str, *, port: int = 8055, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._next_id = 1
        self._lock = threading.Lock()

    def connect(self) -> None:
        if self._socket is not None:
            raise EliteEcError("客户端已经连接")
        self._socket = socket.create_connection((self.host, self.port), self.timeout)
        self._socket.settimeout(self.timeout)

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
                self._buffer.clear()

    def __enter__(self) -> "EliteEcClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        if self._socket is None:
            raise EliteEcError("客户端未连接")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            request: dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": method,
                "id": request_id,
            }
            if params is not None:
                request["params"] = dict(params)
            payload = (
                json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            try:
                self._socket.sendall(payload)
                response = json.loads(self._recv_line().decode("utf-8"))
            except (OSError, TimeoutError, json.JSONDecodeError) as exc:
                raise EliteEcError(
                    f"{method} 结果不明确，运动类请求禁止自动重发"
                ) from exc
        if response.get("id") != request_id:
            raise EliteEcError(
                f"请求/响应ID不匹配：request={request_id}, response={response.get('id')}"
            )
        if "error" in response:
            raise EliteEcError(f"{method} 被控制器拒绝：{response['error']}")
        if "result" not in response:
            raise EliteEcError(f"{method} 响应缺少 result：{response!r}")
        return response["result"]

    def get_robot_mode(self) -> Any:
        return self.call("getRobotMode")

    def get_robot_state(self) -> Any:
        return self.call("getRobotState")

    def clear_alarm(self, *, confirmed: bool = False) -> Any:
        if not confirmed:
            raise PermissionError("clearAlarm 必须由维护人员显式确认")
        return self.call("clearAlarm")

    def set_servo_enabled(self, enabled: bool, *, confirmed: bool = False) -> Any:
        if not confirmed:
            raise PermissionError("伺服状态改变必须由维护人员显式确认")
        return self.call("set_servo_status", {"status": 1 if enabled else 0})

    def move_by_joint(
        self,
        target_joint_deg: Iterable[float],
        *,
        speed: float = 10,
        acc: float = 10,
        dec: float = 10,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> Any:
        """发送 EC/EA 关节目标。

        cond_type=0 表示不使用 UNTIL 条件；cond_num/cond_value保留厂商示例要求的字段。
        若你的版本手册定义不同，必须同步修改并增加对应离线测试。
        """
        require_motion_confirmation(execute, confirmation)
        joint = vector6(target_joint_deg, name="target_joint_deg")
        params = self.build_move_by_joint_params(
            joint, speed=speed, acc=acc, dec=dec
        )
        return self.call("moveByJoint", params)

    @staticmethod
    def build_move_by_joint_params(
        target_joint_deg: Iterable[float],
        *,
        speed: float = 10,
        acc: float = 10,
        dec: float = 10,
    ) -> dict[str, Any]:
        joint = vector6(target_joint_deg, name="target_joint_deg")
        speed_value = in_closed_range(speed, 1, 100, name="speed")
        acc_value = in_closed_range(acc, 1, 100, name="acc")
        dec_value = in_closed_range(dec, 1, 100, name="dec")
        return {
            "targetPos": list(joint),
            "speed": speed_value,
            "acc": acc_value,
            "dec": dec_value,
            "cond_type": 0,
            "cond_num": 7,
            "cond_value": 1,
        }

    def _recv_line(self) -> bytes:
        assert self._socket is not None
        while b"\n" not in self._buffer:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise EliteEcError("机器人关闭8055连接")
            self._buffer.extend(chunk)
        marker = self._buffer.index(ord("\n"))
        line = bytes(self._buffer[:marker])
        del self._buffer[: marker + 1]
        return line


if __name__ == "__main__":
    params = EliteEcClient.build_move_by_joint_params(
        [0, -45, -90, 0, 90, 0], speed=10, acc=10, dec=10
    )
    print(json.dumps(params, ensure_ascii=False, indent=2))
    print(f"真机调用时还必须传 confirmation={MOTION_CONFIRM_TEXT!r}")
