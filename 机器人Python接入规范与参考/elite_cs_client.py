"""艾利特 CS/ES Dashboard(29999) + 脚本端口(30001)最小客户端。

30001发送脚本只代表已发送，不代表运动完成。生产程序必须结合RTSI/寄存器握手或官方
SDK确认最终状态。连续点应放入同一个 def，避免新 def 中止正在执行的旧 def。
"""

from __future__ import annotations

import re
import socket
import threading
from collections.abc import Iterable, Sequence

from common import (
    MOTION_CONFIRM_TEXT,
    format_number,
    in_closed_range,
    require_motion_confirmation,
    vector6,
)


class EliteCsError(RuntimeError):
    pass


class EliteCsClient:
    def __init__(
        self,
        host: str,
        *,
        dashboard_port: int = 29999,
        script_port: int = 30001,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.dashboard_port = dashboard_port
        self.script_port = script_port
        self.timeout = timeout
        self._dashboard: socket.socket | None = None
        self.dashboard_banner = ""
        self._dashboard_lock = threading.Lock()

    def connect_dashboard(self) -> None:
        if self._dashboard is not None:
            raise EliteCsError("Dashboard已经连接")
        channel = socket.create_connection(
            (self.host, self.dashboard_port), self.timeout
        )
        channel.settimeout(self.timeout)
        try:
            # 官方Dashboard连接后先发送欢迎信息；先消费它，避免第一次命令误读成banner。
            self.dashboard_banner = channel.recv(4096).decode(
                "utf-8", errors="replace"
            )
        except BaseException:
            channel.close()
            raise
        self._dashboard = channel

    def close(self) -> None:
        if self._dashboard is not None:
            try:
                self._dashboard.close()
            finally:
                self._dashboard = None
                self.dashboard_banner = ""

    def dashboard(self, command: str) -> str:
        if self._dashboard is None:
            raise EliteCsError("Dashboard未连接")
        if "\n" in command or "\r" in command:
            raise ValueError("单次Dashboard命令不得包含换行")
        with self._dashboard_lock:
            self._dashboard.sendall((command + "\n").encode("utf-8"))
            return self._dashboard.recv(4096).decode("utf-8", errors="replace")

    def power_on(self, *, confirmed: bool = False) -> str:
        if not confirmed:
            raise PermissionError("上电必须由维护人员显式确认")
        return self.dashboard("robotControl -on")

    def brake_release(self, *, confirmed: bool = False) -> str:
        if not confirmed:
            raise PermissionError("释放抱闸必须由维护人员显式确认")
        return self.dashboard("brakeRelease")

    def stop(self) -> str:
        return self.dashboard("stop")

    def movej_joint(
        self,
        joint_rad: Iterable[float],
        *,
        acceleration_rad_s2: float = 0.3,
        velocity_rad_s: float = 0.15,
        blend_radius_m: float = 0.0,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> None:
        require_motion_confirmation(execute, confirmation)
        script = self.build_movej_script(
            joint_rad,
            acceleration_rad_s2=acceleration_rad_s2,
            velocity_rad_s=velocity_rad_s,
            blend_radius_m=blend_radius_m,
        )
        self.send_script(script)

    def movel_pose(
        self,
        pose_m_rad: Iterable[float],
        *,
        acceleration_m_s2: float = 0.1,
        velocity_m_s: float = 0.05,
        blend_radius_m: float = 0.0,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> None:
        require_motion_confirmation(execute, confirmation)
        script = self.build_movel_script(
            pose_m_rad,
            acceleration_m_s2=acceleration_m_s2,
            velocity_m_s=velocity_m_s,
            blend_radius_m=blend_radius_m,
        )
        self.send_script(script)

    def send_script(self, script: str) -> None:
        if not script.endswith("\n"):
            raise ValueError("CS脚本必须以换行结束")
        if not re.match(r"^\s*def\s+[A-Za-z_]\w*\(\):", script):
            raise ValueError("CS主脚本必须以 def name(): 开始")
        if not re.search(r"\bend\s*\n$", script):
            raise ValueError("CS主脚本必须以 end + 换行结束")
        # 每个脚本使用独立连接，避免30001的二进制状态流与示例命令收发混杂。
        with socket.create_connection(
            (self.host, self.script_port), self.timeout
        ) as channel:
            channel.settimeout(self.timeout)
            channel.sendall(script.encode("utf-8"))

    @staticmethod
    def build_movej_script(
        joint_rad: Iterable[float],
        *,
        acceleration_rad_s2: float = 0.3,
        velocity_rad_s: float = 0.15,
        blend_radius_m: float = 0.0,
        function_name: str = "pc_move",
    ) -> str:
        joint = vector6(joint_rad, name="joint_rad")
        acc = in_closed_range(
            acceleration_rad_s2, 0.001, 20, name="acceleration_rad_s2"
        )
        vel = in_closed_range(
            velocity_rad_s, 0.001, 10, name="velocity_rad_s"
        )
        blend = in_closed_range(blend_radius_m, 0, 1, name="blend_radius_m")
        EliteCsClient._validate_function_name(function_name)
        target = ",".join(format_number(value) for value in joint)
        return (
            f"def {function_name}():\n"
            f"  movej([{target}],a={format_number(acc)},"
            f"v={format_number(vel)},t=0,r={format_number(blend)})\n"
            "end\n"
        )

    @staticmethod
    def build_movel_script(
        pose_m_rad: Iterable[float],
        *,
        acceleration_m_s2: float = 0.1,
        velocity_m_s: float = 0.05,
        blend_radius_m: float = 0.0,
        function_name: str = "pc_move",
    ) -> str:
        pose = vector6(pose_m_rad, name="pose_m_rad")
        acc = in_closed_range(
            acceleration_m_s2, 0.001, 20, name="acceleration_m_s2"
        )
        vel = in_closed_range(velocity_m_s, 0.001, 5, name="velocity_m_s")
        blend = in_closed_range(blend_radius_m, 0, 1, name="blend_radius_m")
        EliteCsClient._validate_function_name(function_name)
        target = ",".join(format_number(value) for value in pose)
        return (
            f"def {function_name}():\n"
            f"  movel(p[{target}],a={format_number(acc)},"
            f"v={format_number(vel)},t=0,r={format_number(blend)})\n"
            "end\n"
        )

    @staticmethod
    def build_multi_point_script(
        statements: Sequence[str],
        *,
        function_name: str = "pc_sequence",
    ) -> str:
        """将多点放在同一个 def 中，避免新脚本中止旧脚本。"""
        EliteCsClient._validate_function_name(function_name)
        if not statements:
            raise ValueError("statements 不得为空")
        body: list[str] = []
        for statement in statements:
            line = statement.strip()
            if not line or "\n" in line or "\r" in line:
                raise ValueError("每条运动语句必须是非空单行")
            if not line.startswith(("movej(", "movel(", "movep(", "movec(")):
                raise ValueError(f"不允许的脚本语句：{line!r}")
            body.append(f"  {line}")
        return f"def {function_name}():\n" + "\n".join(body) + "\nend\n"

    @staticmethod
    def _validate_function_name(value: str) -> None:
        if not re.fullmatch(r"[A-Za-z_]\w*", value):
            raise ValueError(f"非法脚本函数名：{value!r}")


if __name__ == "__main__":
    print(
        EliteCsClient.build_movej_script(
            [0, -1.0, -1.5, -1.0, 1.57, 0],
            acceleration_rad_s2=0.3,
            velocity_rad_s=0.15,
        )
    )
    print(f"真机调用时还必须传 confirmation={MOTION_CONFIRM_TEXT!r}")
