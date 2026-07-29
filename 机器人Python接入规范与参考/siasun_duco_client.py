"""新松/多可 DUCO 六轴协作机器人远程控制 SDK 适配示例。

推荐使用厂家随控制器提供、且版本匹配的 Python SDK，经固定端口 7003 调用。
本文件不包含厂家 SDK，也不把上电、清警或使能隐含在运动方法中。

单点运动固定使用阻塞调用和零融合半径：返回 TaskState.ST_Finished(4) 才视为
正常结束。通信中断或返回 -1 时结果不明确，禁止自动重发运动。
"""

from __future__ import annotations

import importlib
import math
import threading
from collections.abc import Callable, Iterable
from typing import Any

from common import require_motion_confirmation, vector6

DUCO_RPC_PORT = 7003
DUCO_COMMAND_PORT = 2000
DUCO_STATE_PORT = 2001
DUCO_STATE_FRAME_BYTES = 1468
TASK_FINISHED = 4

SdkFactory = Callable[[str, int], Any]


class DucoError(RuntimeError):
    """DUCO 接入错误基类。"""


class DucoConnectionError(DucoError):
    """连接未建立或心跳通道失效。"""


class DucoMotionError(DucoError):
    """控制器返回明确的非正常运动结束状态。"""


class DucoResultUnknown(DucoError):
    """运动可能已发送，但上位机无法确定最终结果。"""


def load_sdk_factory(
    module_name: str = "DucoCobotApi_py.DucoCobot",
    class_name: str = "DucoCobot",
) -> SdkFactory:
    """从厂家 SDK 目录加载 DucoCobot 类。

    厂家交付包的顶层目录可能不同；请按实际 SDK 调整 ``module_name``，不要从
    PyPI 安装名称相近但用途无关的第三方 ``duco`` 包。
    """
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise DucoConnectionError(
            "无法加载新松 DUCO Python SDK；请向厂家/集成商取得与 Core 版本"
            f"匹配的 SDK，并核对模块 {module_name!r}、类 {class_name!r}"
        ) from exc
    if not callable(factory):
        raise DucoConnectionError(f"{module_name}.{class_name} 不是可调用的 SDK 类")
    return factory


class DucoSdkClient:
    """对厂家 ``DucoCobot`` 对象做安全边界封装。"""

    def __init__(
        self,
        host: str,
        sdk_factory: SdkFactory,
        *,
        port: int = DUCO_RPC_PORT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self._factory = sdk_factory
        self._api: Any | None = None
        self._motion_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_ready = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_error: Exception | None = None

    def connect(
        self,
        *,
        start_heartbeat: bool = True,
        heartbeat_timeout_ms: int = 1000,
    ) -> None:
        if self._api is not None:
            raise DucoConnectionError("客户端已经连接")
        api = self._factory(self.host, self.port)
        try:
            result = api.open()
        except Exception as exc:
            raise DucoConnectionError("DUCO 7003 主连接建立失败") from exc
        if result != 0:
            raise DucoConnectionError(f"DUCO 7003 主连接建立失败，open()={result!r}")
        self._api = api
        if start_heartbeat:
            try:
                self._start_heartbeat(heartbeat_timeout_ms)
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._heartbeat_thread = None
        self._heartbeat_ready.clear()
        if self._api is not None:
            try:
                self._api.close()
            finally:
                self._api = None

    def __enter__(self) -> "DucoSdkClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def movej_joint(
        self,
        joint_rad: Iterable[float],
        *,
        velocity_rad_s: float = 0.10,
        acceleration_rad_s2: float = 0.20,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> int:
        """按关节运动到六轴目标，单位 rad、rad/s、rad/s²。"""
        require_motion_confirmation(execute, confirmation)
        args = self.build_movej_args(
            joint_rad,
            velocity_rad_s=velocity_rad_s,
            acceleration_rad_s2=acceleration_rad_s2,
        )
        return self._motion("movej2", *args)

    def movej_pose(
        self,
        pose_m_rad: Iterable[float],
        *,
        q_near_rad: Iterable[float],
        tool: str = "default",
        wobj: str = "default",
        velocity_rad_s: float = 0.10,
        acceleration_rad_s2: float = 0.20,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> int:
        """按关节运动到末端位姿，并用 q_near 固定逆解分支。"""
        require_motion_confirmation(execute, confirmation)
        args = self.build_movej_pose_args(
            pose_m_rad,
            q_near_rad=q_near_rad,
            tool=tool,
            wobj=wobj,
            velocity_rad_s=velocity_rad_s,
            acceleration_rad_s2=acceleration_rad_s2,
        )
        return self._motion("movej_pose2", *args)

    def movel_pose(
        self,
        pose_m_rad: Iterable[float],
        *,
        q_near_rad: Iterable[float],
        tool: str = "default",
        wobj: str = "default",
        velocity_m_s: float = 0.03,
        acceleration_m_s2: float = 0.05,
        execute: bool = False,
        confirmation: str | None = None,
    ) -> int:
        """沿笛卡尔直线运动到末端位姿，单位 m、rad、m/s、m/s²。"""
        require_motion_confirmation(execute, confirmation)
        args = self.build_movel_args(
            pose_m_rad,
            q_near_rad=q_near_rad,
            tool=tool,
            wobj=wobj,
            velocity_m_s=velocity_m_s,
            acceleration_m_s2=acceleration_m_s2,
        )
        return self._motion("movel", *args)

    def stop(self) -> Any:
        """使用独立 DucoCobot 对象发送阻塞 stop，避免与运动阻塞调用争用。"""
        control = self._factory(self.host, self.port)
        try:
            opened = control.open()
            if opened != 0:
                raise DucoConnectionError(
                    f"DUCO 独立停止通道建立失败，open()={opened!r}"
                )
            return control.stop(True)
        except DucoError:
            raise
        except Exception as exc:
            raise DucoConnectionError("DUCO stop 调用失败，需立即核对机器人现场状态") from exc
        finally:
            try:
                control.close()
            except Exception:
                pass

    @staticmethod
    def build_movej_args(
        joint_rad: Iterable[float],
        *,
        velocity_rad_s: float = 0.10,
        acceleration_rad_s2: float = 0.20,
    ) -> tuple[list[float], float, float, float, bool]:
        joint = list(_joint_vector(joint_rad, name="joint_rad"))
        velocity = _closed_range(
            velocity_rad_s,
            math.pi / 18000,
            1.25 * math.pi,
            name="velocity_rad_s",
        )
        acceleration = _at_least(
            acceleration_rad_s2,
            math.pi / 18000,
            name="acceleration_rad_s2",
        )
        return joint, velocity, acceleration, 0.0, True

    @staticmethod
    def build_movej_pose_args(
        pose_m_rad: Iterable[float],
        *,
        q_near_rad: Iterable[float],
        tool: str = "default",
        wobj: str = "default",
        velocity_rad_s: float = 0.10,
        acceleration_rad_s2: float = 0.20,
    ) -> tuple[list[float], float, float, float, list[float], str, str, bool]:
        pose = list(_pose_vector(pose_m_rad))
        q_near = list(_joint_vector(q_near_rad, name="q_near_rad"))
        velocity = _closed_range(
            velocity_rad_s,
            math.pi / 18000,
            1.25 * math.pi,
            name="velocity_rad_s",
        )
        acceleration = _at_least(
            acceleration_rad_s2,
            math.pi / 18000,
            name="acceleration_rad_s2",
        )
        return (
            pose,
            velocity,
            acceleration,
            0.0,
            q_near,
            _frame_name(tool, name="tool"),
            _frame_name(wobj, name="wobj"),
            True,
        )

    @staticmethod
    def build_movel_args(
        pose_m_rad: Iterable[float],
        *,
        q_near_rad: Iterable[float],
        tool: str = "default",
        wobj: str = "default",
        velocity_m_s: float = 0.03,
        acceleration_m_s2: float = 0.05,
    ) -> tuple[list[float], float, float, float, list[float], str, str, bool]:
        pose = list(_pose_vector(pose_m_rad))
        q_near = list(_joint_vector(q_near_rad, name="q_near_rad"))
        velocity = _closed_range(
            velocity_m_s, 0.01, 5.0, name="velocity_m_s"
        )
        acceleration = _at_least(
            acceleration_m_s2, 0.01, name="acceleration_m_s2"
        )
        return (
            pose,
            velocity,
            acceleration,
            0.0,
            q_near,
            _frame_name(tool, name="tool"),
            _frame_name(wobj, name="wobj"),
            True,
        )

    def _motion(self, method_name: str, *args: object) -> int:
        api = self._require_api()
        with self._motion_lock:
            try:
                result = getattr(api, method_name)(*args)
            except Exception as exc:
                raise DucoResultUnknown(
                    f"{method_name} 调用异常；机器人可能已经运动，禁止自动重发"
                ) from exc
        if result == -1:
            raise DucoResultUnknown(
                f"{method_name} 返回 -1，通信已断开；禁止自动重发"
            )
        if result != TASK_FINISHED:
            raise DucoMotionError(
                f"{method_name} 未正常完成：TaskState={result!r}，期望 4(ST_Finished)"
            )
        return int(result)

    def _require_api(self) -> Any:
        if self._api is None:
            raise DucoConnectionError("DUCO 客户端未连接")
        if self._heartbeat_error is not None:
            raise DucoConnectionError(
                f"DUCO 心跳线程已失败：{self._heartbeat_error}"
            )
        return self._api

    def _start_heartbeat(self, timeout_ms: int) -> None:
        timeout = int(timeout_ms)
        if not 200 <= timeout <= 60000:
            raise ValueError("heartbeat_timeout_ms 必须在 [200, 60000]")
        self._heartbeat_stop.clear()
        self._heartbeat_ready.clear()
        self._heartbeat_error = None

        def worker() -> None:
            heartbeat = self._factory(self.host, self.port)
            try:
                opened = heartbeat.open()
                if opened != 0:
                    raise DucoConnectionError(
                        f"DUCO 心跳通道建立失败，open()={opened!r}"
                    )
                heartbeat.rpc_heartbeat(timeout)
                self._heartbeat_ready.set()
                interval = max(0.1, timeout / 2000)
                while not self._heartbeat_stop.wait(interval):
                    heartbeat.rpc_heartbeat(timeout)
            except Exception as exc:
                self._heartbeat_error = exc
                self._heartbeat_ready.set()
            finally:
                try:
                    heartbeat.close()
                except Exception:
                    pass

        self._heartbeat_thread = threading.Thread(
            target=worker,
            name=f"duco-heartbeat-{self.host}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        if not self._heartbeat_ready.wait(timeout=3.0):
            raise DucoConnectionError("DUCO 心跳线程启动超时")
        if self._heartbeat_error is not None:
            raise DucoConnectionError(
                f"DUCO 心跳线程启动失败：{self._heartbeat_error}"
            )


def _joint_vector(values: Iterable[float], *, name: str) -> tuple[float, ...]:
    result = vector6(values, name=name)
    limit = 2 * math.pi
    if any(not -limit <= value <= limit for value in result):
        raise ValueError(f"{name} 的每个关节必须在 [-2π, 2π] rad")
    return result


def _pose_vector(values: Iterable[float]) -> tuple[float, ...]:
    result = vector6(values, name="pose_m_rad")
    limit = 2 * math.pi
    if any(not -limit <= value <= limit for value in result[3:]):
        raise ValueError("pose_m_rad 的 Rx/Ry/Rz 必须在 [-2π, 2π] rad")
    return result


def _closed_range(value: float, low: float, high: float, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} 必须在 [{low}, {high}]，当前为 {value!r}")
    return number


def _at_least(value: float, low: float, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < low:
        raise ValueError(f"{name} 必须是不小于 {low} 的有限值，当前为 {value!r}")
    return number


def _frame_name(value: str, *, name: str) -> str:
    text = str(value)
    if not text or any(char in text for char in "\r\n\x00"):
        raise ValueError(f"{name} 必须是非空且不含控制换行的坐标系名称")
    return text

