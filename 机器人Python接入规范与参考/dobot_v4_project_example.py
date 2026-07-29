"""复用本工程越疆 V4 生产驱动的最小示例。

不要把本文件里的占位点直接用于真机。生产业务应通过 RobotController 的命名点接口，
而不是直接调用 transport。
"""

from __future__ import annotations

from collections.abc import Iterable

from common import require_motion_confirmation, vector6
from eit_ptlc.driver.dobot_tcp_driver import DobotTcpRobotTransport
from eit_ptlc.driver.robot_transport import MotionOptions, RobotFeedback


def move_validated_joint_point(
    host: str,
    *,
    pose_mm_deg: Iterable[float],
    joint_deg: Iterable[float],
    user: int,
    tool: int,
    execute: bool,
    confirmation: str | None,
) -> RobotFeedback:
    """以已标定关节解执行 MovJ；连接后不自动使能或清警。"""
    require_motion_confirmation(execute, confirmation)
    pose = vector6(pose_mm_deg, name="pose_mm_deg")
    joint = vector6(joint_deg, name="joint_deg")
    options = MotionOptions(user=user, tool=tool, acc=10, vel=10, cp=0)
    with DobotTcpRobotTransport(host, speed_factor=10) as robot:
        return robot.move_j(pose, options, joint=joint)


def move_validated_linear_point(
    host: str,
    *,
    pose_mm_deg: Iterable[float],
    user: int,
    tool: int,
    execute: bool,
    confirmation: str | None,
) -> RobotFeedback:
    """以已验收笛卡尔位姿执行 MovL。"""
    require_motion_confirmation(execute, confirmation)
    pose = vector6(pose_mm_deg, name="pose_mm_deg")
    options = MotionOptions(user=user, tool=tool, acc=10, vel=10, cp=0)
    with DobotTcpRobotTransport(host, speed_factor=10) as robot:
        return robot.move_l(pose, options)


if __name__ == "__main__":
    print("本文件不会自动连接或运动；请从已审核点位表调用上面的函数。")
