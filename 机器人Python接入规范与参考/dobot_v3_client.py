"""越疆 CR/Nova V3 TCP 文本协议最小示例。

适用于对应 V3 控制器手册的机器人。生产项目优先使用厂商 SDK，并增加30004状态对账。
本示例不自动使能、不自动清警、不自动重发运动。
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterable

from common import (
    MOTION_CONFIRM_TEXT,
    format_number,
    in_closed_range,
    require_motion_confirmation,
    vector6,
)


class DobotV3Error(RuntimeError):
    pass


class DobotV3Client:
    def __init__(
        self,
        host: str,
        *,
        dashboard_port: int = 29999,
        motion_port: int = 30003,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.dashboard_port = dashboard_port
        self.motion_port = motion_port
        self.timeout = timeout
        self._dashboard: socket.socket | None = None
        self._motion: socket.socket | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        if self._dashboard is not None or self._motion is not None:
            raise DobotV3Error("客户端已经连接")
        try:
            self._dashboard = socket.create_connection(
                (self.host, self.dashboard_port), self.timeout
            )
            self._motion = socket.create_connection(
                (self.host, self.motion_port), self.timeout
            )
            self._dashboard.settimeout(self.timeout)
            self._motion.settimeout(self.timeout)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        for channel in (self._motion, self._dashboard):
            if channel is not None:
                try:
                    channel.close()
                except OSError:
                    pass
        self._dashboard = None
        self._motion = None

    def __enter__(self) -> "DobotV3Client":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def robot_mode(self) -> str:
        return self._request(self._dashboard, "RobotMode()")

    def clear_error(self, *, confirmed: bool = False) -> str:
        if not confirmed:
            raise PermissionError("ClearError 必须由维护人员显式确认")
        return self._request(self._dashboard, "ClearError()")

    def enable_robot(self, *, confirmed: bool = False) -> str:
        if not confirmed:
            raise PermissionError("EnableRobot 必须由维护人员显式确认")
        return self._request(self._dashboard, "EnableRobot()")

    def movj_pose(
        self,
        pose_mm_deg: Iterable[float],
        *,
        user: int = 0,
        tool: int = 0,
        speed_percent: float = 10,
        acc_percent: float = 10,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> str:
        require_motion_confirmation(execute, confirmation)
        command = self.build_movj_pose(
            pose_mm_deg,
            user=user,
            tool=tool,
            speed_percent=speed_percent,
            acc_percent=acc_percent,
        )
        return self._request(self._motion, command)

    def movl_pose(
        self,
        pose_mm_deg: Iterable[float],
        *,
        user: int = 0,
        tool: int = 0,
        speed_percent: float = 10,
        acc_percent: float = 10,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> str:
        require_motion_confirmation(execute, confirmation)
        command = self.build_movl_pose(
            pose_mm_deg,
            user=user,
            tool=tool,
            speed_percent=speed_percent,
            acc_percent=acc_percent,
        )
        return self._request(self._motion, command)

    def joint_movj(
        self,
        joint_deg: Iterable[float],
        *,
        speed_percent: float = 10,
        acc_percent: float = 10,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> str:
        require_motion_confirmation(execute, confirmation)
        command = self.build_joint_movj(
            joint_deg,
            speed_percent=speed_percent,
            acc_percent=acc_percent,
        )
        return self._request(self._motion, command)

    def sync(self) -> str:
        """阻塞等待运动队列完成；仍建议结合30004反馈核对实际位置。"""
        return self._request(self._motion, "Sync()")

    @staticmethod
    def build_movj_pose(
        pose_mm_deg: Iterable[float],
        *,
        user: int = 0,
        tool: int = 0,
        speed_percent: float = 10,
        acc_percent: float = 10,
    ) -> str:
        pose = vector6(pose_mm_deg, name="pose_mm_deg")
        speed = in_closed_range(speed_percent, 1, 100, name="SpeedJ")
        acc = in_closed_range(acc_percent, 1, 100, name="AccJ")
        args = ",".join(format_number(value) for value in pose)
        return (
            f"MovJ({args},User={int(user)},Tool={int(tool)},"
            f"SpeedJ={format_number(speed)},AccJ={format_number(acc)})"
        )

    @staticmethod
    def build_movl_pose(
        pose_mm_deg: Iterable[float],
        *,
        user: int = 0,
        tool: int = 0,
        speed_percent: float = 10,
        acc_percent: float = 10,
    ) -> str:
        pose = vector6(pose_mm_deg, name="pose_mm_deg")
        speed = in_closed_range(speed_percent, 1, 100, name="SpeedL")
        acc = in_closed_range(acc_percent, 1, 100, name="AccL")
        args = ",".join(format_number(value) for value in pose)
        return (
            f"MovL({args},User={int(user)},Tool={int(tool)},"
            f"SpeedL={format_number(speed)},AccL={format_number(acc)})"
        )

    @staticmethod
    def build_joint_movj(
        joint_deg: Iterable[float],
        *,
        speed_percent: float = 10,
        acc_percent: float = 10,
    ) -> str:
        joint = vector6(joint_deg, name="joint_deg")
        speed = in_closed_range(speed_percent, 1, 100, name="SpeedJ")
        acc = in_closed_range(acc_percent, 1, 100, name="AccJ")
        args = ",".join(format_number(value) for value in joint)
        return (
            f"JointMovJ({args},SpeedJ={format_number(speed)},"
            f"AccJ={format_number(acc)})"
        )

    def _request(self, channel: socket.socket | None, command: str) -> str:
        if channel is None:
            raise DobotV3Error("客户端未连接")
        with self._lock:
            try:
                channel.sendall(command.encode("ascii"))
                response = self._recv_until(channel, b";").decode(
                    "ascii", errors="replace"
                )
            except (OSError, TimeoutError) as exc:
                # 运动请求可能已经到达控制器，绝不在这里重发。
                raise DobotV3Error(
                    f"命令结果不明确，禁止自动重发：{command}"
                ) from exc
        if not response.lstrip().startswith("0,"):
            raise DobotV3Error(response)
        return response

    @staticmethod
    def _recv_until(channel: socket.socket, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = channel.recv(4096)
            if not chunk:
                raise DobotV3Error("机器人关闭连接")
            data.extend(chunk)
        return bytes(data[: data.index(marker) + len(marker)])


if __name__ == "__main__":
    # 只打印报文，不连接机器人。
    print(
        DobotV3Client.build_movj_pose(
            [400, 0, 300, 180, 0, 0], speed_percent=10, acc_percent=10
        )
    )
    print(f"真机调用时还必须传 confirmation={MOTION_CONFIRM_TEXT!r}")
